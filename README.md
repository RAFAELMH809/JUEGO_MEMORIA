# Juego de Memoria Distribuido con gRPC - Servidor

Implementacion completa de la parte servidor para un juego de memoria distribuido, construida con Python 3.11, gRPC, concurrencia con threading, persistencia JSON y panel web en tiempo real con FastAPI + SSE.

## 1. Resumen de arquitectura

El servidor esta separado en capas para evitar mezclar transporte, logica de juego y visualizacion:

- Capa de contrato: `proto/memory_game.proto`
- Capa gRPC: `server/app/grpc_server.py`
- Capa de dominio/logica: `server/app/game_engine.py`
- Capa de persistencia: `server/app/storage.py`
- Capa de eventos en tiempo real: `server/app/broadcaster.py`
- Capa web de monitoreo: `server/app/web.py` + templates/static
- Arranque y composicion: `server/main.py`

### Decisiones tecnicas clave

1. Estado global centralizado en `GameEngine` protegido con `threading.Lock()`.
2. Streaming real sin polling para clientes gRPC usando `SubscribeToUpdates` (server-streaming).
3. Difusion robusta por suscriptor con `queue.Queue`, evitando bloquear el juego por un consumidor lento.
4. Miss con revelado temporal no bloqueante mediante hilo daemon (`_resolve_miss`) y bandera `pending_miss`.
5. Persistencia simple, segura y trazable en JSON (`matches_history.json`).
6. Panel web sincronizado en vivo por SSE (`/events`) y APIs JSON auxiliares (`/api/state`, `/api/stats`, `/api/history`).
7. Regla de turnos: si hay match, el jugador conserva turno (documentado para coherencia de gameplay clasico).

## 2. Arbol final de carpetas

```text
memory-grpc/
├── proto/
│   └── memory_game.proto
├── generated/
│   ├── __init__.py
│   ├── memory_game_pb2.py
│   └── memory_game_pb2_grpc.py
├── server/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── broadcaster.py
│   │   ├── config.py
│   │   ├── game_engine.py
│   │   ├── grpc_server.py
│   │   ├── models.py
│   │   ├── storage.py
│   │   ├── utils.py
│   │   └── web.py
│   ├── data/
│   │   └── matches_history.json
│   ├── static/
│   │   ├── css/
│   │   │   └── styles.css
│   │   └── js/
│   │       └── app.js
│   ├── templates/
│   │   └── index.html
│   ├── main.py
│   └── requirements.txt
├── scripts/
│   ├── generate_proto.ps1
│   └── generate_proto.sh
├── Dockerfile.server
├── docker-compose.yml
└── README.md
```

## 3. Contrato gRPC (.proto)

Archivo: `proto/memory_game.proto`

Incluye:

- RPCs: `JoinGame`, `GetBoardState`, `PlayTurn`, `SubscribeToUpdates`, `GetStats`, `GetMatchHistory`
- Mensajes de dominio: tablero publico, jugadores, eventos, snapshot global
- Enums para estado del juego y tipo de evento
- Mensajes para historial persistido

## 4. Modelo de datos y estado del juego

### Jugador

Se almacena por jugador:

- `player_id` (UUID)
- `name`
- `score`
- `moves`
- `pairs_found`
- `response_times`
- `total_response_time`
- `average_response_time` (derivado)
- `turn_history`

### Tablero

- Interno real: `board_values` (simbolos) + `board_states` (`hidden`, `revealed`, `matched`)
- Publico: celdas con `value` visible solo si esta revelada o emparejada; oculta muestra `■`
- Restricciones: filas/columnas pares, rango 4..8

### Turnos

- Solo juega quien tiene turno activo
- Cada jugada valida 2 coordenadas distintas y validas
- Match: suma score/pareja, conserva turno
- Miss: revela temporalmente, bloquea jugadas con `pending_miss`, oculta tras delay configurable y rota turno

## 5. Streaming en tiempo real

### gRPC

- RPC `SubscribeToUpdates` envia `GameUpdate` por stream server-side
- Eventos emitidos en:
  - join
  - start
  - turn_changed
  - turn_played
  - match
  - miss_revealed
  - miss_hidden
  - game_over
  - stats_updated

### Panel web

- Endpoint SSE: `GET /events`
- Frontend consume eventos en vivo y actualiza:
  - tablero
  - jugadores
  - turno
  - eventos
  - ranking
  - historial

## 6. Persistencia JSON

Archivo: `server/data/matches_history.json`

Se guarda por partida:

- `match_id`
- timestamps `started_at` y `finished_at`
- `rows`, `cols`
- `status`
- `winners`
- lista completa de jugadores con metricas
- historial de eventos

Consulta disponible via:

- gRPC: `GetMatchHistory`
- HTTP: `/api/history`

## 7. Ejecucion local paso a paso

### Requisitos

- Python 3.11+
- pip

### Instalacion

```bash
cd memory-grpc
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows PowerShell
pip install -r server/requirements.txt
```

### Generar stubs protobuf

Linux/macOS:

```bash
bash scripts/generate_proto.sh
```

Windows PowerShell:

```powershell
./scripts/generate_proto.ps1
```

### Ejecutar servidor

```bash
python server/main.py
```

Servicios disponibles:

- gRPC: `0.0.0.0:50051`
- Panel web: `http://localhost:8000`

## 8. Ejecucion con Docker

```bash
cd memory-grpc
docker compose up --build
```

Se exponen:

- `50051` para gRPC
- `8000` para panel web

Configuraciones clave por variable de entorno:

- `MIN_PLAYERS`
- `BOARD_ROWS`
- `BOARD_COLS`
- `REVEAL_DELAY_SECONDS`
- `GRPC_MAX_WORKERS`

## 9. Ejemplos de respuestas esperadas

### JoinGame (respuesta exitosa)

```json
{
  "accepted": true,
  "reason": "Jugador registrado",
  "player_id": "ad997cb9-3f9a-4f8d-8a1c-3d8c8ff24ad3",
  "snapshot": {
    "status": "WAITING_FOR_PLAYERS",
    "connected_players": 1
  }
}
```

### PlayTurn (miss)

```json
{
  "accepted": true,
  "reason": "No hubo pareja",
  "matched": false,
  "game_over": false
}
```

### Update de stream (match)

```json
{
  "event_type": "MATCH_FOUND",
  "message": "Ana encontro pareja 🍎 y conserva el turno",
  "snapshot": {
    "current_turn_player_name": "Ana",
    "remaining_pairs": 5
  }
}
```

## 10. Ejemplo de logs esperados

```text
2026-04-15 19:40:12 | INFO | memory.main | Servidor gRPC escuchando en 0.0.0.0:50051
INFO:     Uvicorn running on http://0.0.0.0:8000
2026-04-15 19:40:28 | INFO | memory.grpc | Nuevo suscriptor de stream id=... player_id=... nombre=cliente-1
2026-04-15 19:41:01 | INFO | memory.grpc | Suscriptor stream cerrado id=...
```

## 11. Como conectar despues multiples clientes gRPC

1. Compilar el mismo `proto/memory_game.proto` en cada cliente.
2. Crear canal a `host_servidor:50051`.
3. Flujo recomendado de cliente:
   - `JoinGame`
   - abrir stream `SubscribeToUpdates` en paralelo
   - consultar `GetBoardState` para sync inicial si se desea
   - enviar jugadas con `PlayTurn` cuando el stream indique turno
4. Para mostrar resultados/historial:
   - `GetStats`
   - `GetMatchHistory`

La arquitectura ya esta lista para 3+ clientes concurrentes, incluyendo despliegue en LAN o Docker network.

## 12. Endpoints HTTP del panel

- `GET /` interfaz de jugadores (juego web)
- `GET /admin` panel administrativo
- `GET /events` SSE para jugadores
- `GET /events/admin` SSE para panel admin
- `GET /api/state` snapshot publico
- `GET /api/state/admin` snapshot admin (incluye tablero real)
- `POST /api/join` registro/reconexion de jugador web
- `POST /api/preview` primera carta (giro previo a segunda seleccion)
- `POST /api/play` jugada completa (2 cartas)
- `POST /api/admin/start` inicio manual por administrador
- `POST /api/admin/reset` reinicio total de ronda por administrador
- `POST /api/admin/board-size` seleccion manual de tablero (`4`, `6`, `8`)
- `POST /api/admin/board-size/auto` volver a modo automatico por cantidad de jugadores
- `GET /api/stats` estadisticas y ranking
- `GET /api/history` historial persistido

## 13. Checklist de cumplimiento de la rubrica

### Configuracion y arquitectura

- [x] .proto creado, completo y compilable
- [x] servidor preparado para multiples conexiones
- [x] integracion con docker-compose
- [x] red docker compartida (servicio publicable por puertos)

### Funcionalidad del servidor

- [x] ID unico por cliente (UUID)
- [x] estado global del tablero
- [x] control de turnos
- [x] validacion de jugadas
- [x] actualizacion en tiempo real a suscriptores
- [x] deteccion de fin del juego
- [x] registro de score, tiempos y desempeno por jugador

### Sincronizacion y red

- [x] streaming real gRPC server-streaming
- [x] estructura apta para varios clientes simultaneos
- [x] servidor escuchando en 0.0.0.0
- [x] puertos claros y configurables
- [x] listo para pruebas en LAN

### Persistencia y consulta historica

- [x] almacenamiento JSON de partidas
- [x] consulta de historial por RPC (`GetMatchHistory`)
- [x] consulta de estadisticas por RPC (`GetStats`)
- [x] consulta equivalente via panel web

## 14. Kit listo para tus companeros (clientes)

Se agrego la carpeta compartible:

- `clients/python-client`

Incluye:

- `client.py` (cliente CLI interactivo real)
- `proto/memory_game.proto`
- `generated/` para stubs
- `scripts/generate_proto.ps1` y `scripts/generate_proto.sh`
- `requirements.txt`
- `Dockerfile.client`
- `README.md` con pasos exactos

Flujo para cada companero:

1. Copiar carpeta `clients/python-client`.
2. Ejecutar `pip install -r requirements.txt`.
3. Generar stubs con `./scripts/generate_proto.ps1` (Windows) o `bash scripts/generate_proto.sh` (Linux/macOS).
4. Correr cliente apuntando al host del servidor:

```bash
python client.py --host <IP_DEL_SERVIDOR> --port 50051 --name <NOMBRE>
```

Nota de red:

- Si el servidor corre en Docker en tu maquina, tus companeros deben usar tu IP LAN (ejemplo `192.168.1.40`), no `localhost`.

### Documentacion y evidencia

- [x] README tecnico y claro
- [x] explicacion de arquitectura y decisiones
- [x] instrucciones de ejecucion local y Docker
- [x] ejemplos de respuestas y logs

### Queda preparado para la parte cliente

- [x] contrato RPC estable para clientes
- [x] stream de eventos para sincronizacion de UI cliente
- [x] validaciones y reglas del juego ya centralizadas en servidor
- [x] panel web de monitoreo para depuracion durante desarrollo cliente

## 15. Logica actual de juego web (admin + jugadores)

### Flujo de sala

1. Los jugadores entran por `GET /` y se registran con nombre.
2. El servidor permite maximo 4 jugadores en sala (`MAX_PLAYERS=4`).
3. Mientras el estado es `WAITING_FOR_PLAYERS`, el admin puede:
  - iniciar partida manualmente,
  - reiniciar sala,
  - quitar jugadores puntuales.

### Tamano de tablero por cantidad de jugadores

- 2 jugadores: `4x4`
- 3 jugadores: `6x6`
- 4 jugadores: `8x8`

Nota tecnica: no se usan `5x5` o `7x7` porque el juego de memoria necesita numero par de celdas para formar parejas completas.

### Turnos y jugada carta por carta

1. El jugador elige una carta (`/api/preview`) y ve su emoji localmente.
2. Elige la segunda carta (`/api/play`).
3. Si hay match, suma punto y conserva turno.
4. Si no hay match, ambas cartas se ocultan tras `REVEAL_DELAY_SECONDS`.
5. Todos reciben actualizacion por SSE/gRPC sin polling.

### Roles de visualizacion

- Jugador (`/`): solo ve tablero publico.
- Admin (`/admin`): ve tablero real con todas las cartas.

## 16. Guia para documentar el reporte PDF

Usa esta estructura para que el equipo pueda documentar rapido:

1. Objetivo del proyecto y alcance.
2. Arquitectura (gRPC, motor de juego, web, persistencia, broadcaster).
3. Diagrama de flujo de eventos (`Join -> Start -> Preview -> Play -> Stream`).
4. Concurrencia y sincronizacion (`threading.Lock`, cola por suscriptor).
5. Red y despliegue (LAN + Docker Compose + puertos).
6. Persistencia (`matches_history.json`, consultas de historial/stats).
7. Evidencia (capturas de `/`, `/admin`, 3 clientes conectados, fin de juego).
8. Pruebas de checklist y resultados.
9. Reflexion tecnica del equipo.

## 17. Checklist operativo alineado a la rubrica

### 1) Configuracion y arquitectura

- [x] .proto compilado y en uso por servidor/clientes
- [x] servidor levantando y aceptando multiples conexiones
- [x] despliegue con Docker Compose
- [x] servicios expuestos para red LAN

### 2) Funcionalidad del servidor

- [x] ID unico por cliente (UUID)
- [x] estado global centralizado del tablero
- [x] control de turnos estricto
- [x] validacion de jugadas y coordenadas
- [x] actualizaciones en tiempo real a todos
- [x] deteccion de fin de juego
- [x] metricas por jugador (score, movimientos, tiempos)

### 3) Funcionalidad del cliente

- [x] cliente gRPC CLI funcional
- [x] cliente web funcional por navegador
- [x] seleccion de dos cartas por turno
- [x] mensajes de estado/errores y fin de juego

### 4) Sincronizacion y red

- [x] streaming gRPC + SSE sin polling
- [x] soporte multi-cliente en paralelo
- [x] funcionamiento en LAN con IP del host

### 5) Documentacion y evidencia

- [x] README tecnico y actualizado
- [x] arquitectura y decisiones documentadas
- [x] pasos de ejecucion local/Docker
- [x] checklist para reporte PDF
