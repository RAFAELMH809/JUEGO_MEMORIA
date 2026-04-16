from __future__ import annotations

import argparse
import threading
from typing import Optional

import grpc

from generated import memory_game_pb2, memory_game_pb2_grpc


def render_board(snapshot: memory_game_pb2.GameSnapshot) -> None:
    rows = snapshot.board.rows
    cols = snapshot.board.cols
    grid = [["?" for _ in range(cols)] for _ in range(rows)]

    for cell in snapshot.board.cells:
        grid[cell.row][cell.col] = cell.value

    print("\nTablero publico:")
    for row in grid:
        print(" ".join(row))
    print()


def print_snapshot(snapshot: memory_game_pb2.GameSnapshot) -> None:
    status_name = memory_game_pb2.GameStatus.Name(snapshot.status)
    print(f"Estado: {status_name}")
    print(f"Jugadores conectados: {snapshot.connected_players}")
    print(f"Turno actual: {snapshot.current_turn_player_name or 'N/A'}")
    print(
        f"Parejas: {snapshot.board.matched_pairs}/{snapshot.board.total_pairs}"
        f" | Restantes: {snapshot.remaining_pairs}"
    )
    render_board(snapshot)


def stream_listener(
    stub: memory_game_pb2_grpc.MemoryGameServiceStub,
    player_id: str,
    player_name: str,
    stop_event: threading.Event,
    snapshot_holder: dict,
    holder_lock: threading.Lock,
) -> None:
    request = memory_game_pb2.SubscribeRequest(
        player_id=player_id,
        subscriber_name=player_name,
    )
    try:
        for update in stub.SubscribeToUpdates(request):
            if stop_event.is_set():
                break
            with holder_lock:
                snapshot_holder["snapshot"] = update.snapshot
            event_name = memory_game_pb2.EventType.Name(update.event_type)
            print(f"\n[STREAM] {event_name}: {update.message}")
            if update.snapshot.game_over:
                winners = ", ".join(update.snapshot.winners) if update.snapshot.winners else "Sin ganador"
                print(f"[STREAM] Juego terminado. Ganador(es): {winners}")
    except grpc.RpcError as exc:
        if not stop_event.is_set():
            print(f"Stream finalizado: {exc.code().name} - {exc.details()}")


def safe_play_turn(
    stub: memory_game_pb2_grpc.MemoryGameServiceStub,
    player_id: str,
    first: tuple[int, int],
    second: tuple[int, int],
) -> None:
    request = memory_game_pb2.PlayTurnRequest(
        player_id=player_id,
        first=memory_game_pb2.Position(row=first[0], col=first[1]),
        second=memory_game_pb2.Position(row=second[0], col=second[1]),
    )
    try:
        response = stub.PlayTurn(request)
        print(f"Respuesta jugada: accepted={response.accepted}, matched={response.matched}, reason={response.reason}")
        print_snapshot(response.snapshot)
    except grpc.RpcError as exc:
        print(f"Jugada rechazada: {exc.code().name} - {exc.details()}")


def show_stats(stub: memory_game_pb2_grpc.MemoryGameServiceStub, match_id: str = "") -> None:
    response = stub.GetStats(memory_game_pb2.StatsRequest(match_id=match_id))
    print("\nRanking:")
    for idx, player in enumerate(response.ranking, start=1):
        print(
            f"{idx}. {player.name} | score={player.score} | moves={player.moves} | "
            f"avg={player.average_response_time:.3f}s"
        )


def show_help() -> None:
    print("\nComandos disponibles:")
    print("  estado")
    print("  jugar <r1> <c1> <r2> <c2>")
    print("  stats")
    print("  historial")
    print("  ayuda")
    print("  salir\n")


def run_cli(host: str, port: int, player_name: str) -> None:
    target = f"{host}:{port}"
    print(f"Conectando al servidor en {target}...")

    with grpc.insecure_channel(target) as channel:
        stub = memory_game_pb2_grpc.MemoryGameServiceStub(channel)

        join = stub.JoinGame(memory_game_pb2.JoinGameRequest(player_name=player_name))
        if not join.accepted:
            print(f"No se pudo unir: {join.reason}")
            return

        player_id = join.player_id
        print(f"Conectado como {player_name}. player_id={player_id}")
        print_snapshot(join.snapshot)

        stop_event = threading.Event()
        snapshot_holder: dict[str, Optional[memory_game_pb2.GameSnapshot]] = {
            "snapshot": join.snapshot
        }
        holder_lock = threading.Lock()

        listener = threading.Thread(
            target=stream_listener,
            args=(stub, player_id, player_name, stop_event, snapshot_holder, holder_lock),
            daemon=True,
        )
        listener.start()

        show_help()

        while True:
            try:
                raw = input("> ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nSaliendo...")
                break

            if not raw:
                continue

            parts = raw.split()
            cmd = parts[0].lower()

            if cmd == "salir":
                break

            if cmd == "ayuda":
                show_help()
                continue

            if cmd == "estado":
                state = stub.GetBoardState(
                    memory_game_pb2.BoardStateRequest(requester_player_id=player_id)
                )
                print_snapshot(state.snapshot)
                continue

            if cmd == "stats":
                show_stats(stub)
                continue

            if cmd == "historial":
                history = stub.GetMatchHistory(
                    memory_game_pb2.MatchHistoryRequest(limit=10)
                )
                print("\nPartidas guardadas:")
                for match in history.matches:
                    winners = ", ".join(match.winners) if match.winners else "Sin ganador"
                    print(
                        f"- {match.match_id[:8]} | {match.rows}x{match.cols} | "
                        f"estado={match.status} | ganadores={winners}"
                    )
                continue

            if cmd == "jugar":
                if len(parts) != 5:
                    print("Uso: jugar <r1> <c1> <r2> <c2>")
                    continue
                try:
                    r1, c1, r2, c2 = map(int, parts[1:])
                except ValueError:
                    print("Coordenadas invalidas. Deben ser enteros.")
                    continue

                safe_play_turn(stub, player_id, (r1, c1), (r2, c2))
                continue

            print("Comando no reconocido. Escribe 'ayuda'.")

        stop_event.set()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cliente gRPC interactivo para el Juego de Memoria Distribuido"
    )
    parser.add_argument("--host", default="127.0.0.1", help="IP o host del servidor")
    parser.add_argument("--port", default=50051, type=int, help="Puerto gRPC del servidor")
    parser.add_argument("--name", required=True, help="Nombre del jugador")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_cli(host=args.host, port=args.port, player_name=args.name)


if __name__ == "__main__":
    main()
