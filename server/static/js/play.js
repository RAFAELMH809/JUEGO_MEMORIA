const state = {
  snapshot: globalThis.__INITIAL_SNAPSHOT__ || null,
  events: [],
  selected: [],
  playerId: localStorage.getItem("memory_player_id") || "",
  playerName: localStorage.getItem("memory_player_name") || ""
};

const el = {
  nameInput: document.getElementById("player-name"),
  joinBtn: document.getElementById("join-btn"),
  joinStatus: document.getElementById("join-status"),
  gameStatus: document.getElementById("game-status"),
  currentTurn: document.getElementById("current-turn"),
  remainingPairs: document.getElementById("remaining-pairs"),
  connectedPlayers: document.getElementById("connected-players"),
  board: document.getElementById("board"),
  playersTbody: document.getElementById("players-tbody"),
  eventLog: document.getElementById("event-log")
};

function setJoinStatus(message) {
  el.joinStatus.textContent = message;
}

function coordKey(row, col) {
  return `${row}:${col}`;
}

function renderBoard(snapshot) {
  if (!snapshot || !snapshot.board) return;

  const board = snapshot.board;
  el.board.style.gridTemplateColumns = `repeat(${board.cols}, minmax(40px, 1fr))`;

  const selectedSet = new Set(state.selected.map((p) => coordKey(p.row, p.col)));

  el.board.innerHTML = board.cells
    .map((cell) => {
      const key = coordKey(cell.row, cell.col);
      const classes = ["cell"];
      if (cell.is_revealed) classes.push("revealed");
      if (cell.is_matched) classes.push("matched");
      if (selectedSet.has(key)) classes.push("selected");

      return `<button class="${classes.join(" ")}" data-row="${cell.row}" data-col="${cell.col}" ${
        cell.is_matched ? "disabled" : ""
      }>${cell.value}</button>`;
    })
    .join("");
}

function renderPlayers(snapshot) {
  const players = snapshot.players || [];
  el.playersTbody.innerHTML = players
    .map(
      (p) => `
      <tr>
        <td>${p.name}</td>
        <td>${p.score}</td>
        <td>${p.moves}</td>
        <td>${p.pairs_found}</td>
        <td>${p.is_current_turn ? "Si" : "No"}</td>
      </tr>
    `
    )
    .join("");
}

function renderEvents() {
  const recent = state.events.slice(-30).reverse();
  el.eventLog.innerHTML = recent
    .map(
      (e) => `
      <div class="event-item">
        <strong>${e.event_type}</strong>
        <div>${e.message}</div>
        <small>${e.timestamp}</small>
      </div>
    `
    )
    .join("");
}

function renderSnapshot(snapshot) {
  if (!snapshot) return;
  state.snapshot = snapshot;

  el.gameStatus.textContent = snapshot.status;
  el.currentTurn.textContent = snapshot.current_turn_player_name || "N/A";
  el.remainingPairs.textContent = snapshot.remaining_pairs;
  el.connectedPlayers.textContent = snapshot.connected_players;

  renderBoard(snapshot);
  renderPlayers(snapshot);
}

async function joinGame() {
  const playerName = (el.nameInput.value || "").trim();
  if (!playerName) {
    setJoinStatus("Escribe tu nombre para entrar.");
    return;
  }

  try {
    const response = await fetch("/api/join", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ player_name: playerName })
    });

    if (!response.ok) {
      const detail = await response.json();
      setJoinStatus(`No se pudo unir: ${detail.detail || "Error"}`);
      return;
    }

    const payload = await response.json();
    state.playerId = payload.player_id;
    state.playerName = playerName;
    localStorage.setItem("memory_player_id", state.playerId);
    localStorage.setItem("memory_player_name", state.playerName);

    setJoinStatus(`Conectado como ${playerName}. Espera tu turno.`);
    renderSnapshot(payload.snapshot);
  } catch (_error) {
    setJoinStatus("Error de red al intentar unirse.");
  }
}

async function playSelected() {
  if (!state.playerId) {
    setJoinStatus("Primero debes unirte al juego.");
    state.selected = [];
    renderSnapshot(state.snapshot);
    return;
  }

  if (state.selected.length !== 2) return;

  const [a, b] = state.selected;

  try {
    const response = await fetch("/api/play", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        player_id: state.playerId,
        first_row: a.row,
        first_col: a.col,
        second_row: b.row,
        second_col: b.col
      })
    });

    if (!response.ok) {
      const detail = await response.json();
      setJoinStatus(detail.detail || "Jugada rechazada.");
      state.selected = [];
      renderSnapshot(state.snapshot);
      return;
    }

    const payload = await response.json();
    setJoinStatus(payload.reason || "Jugada enviada.");
    renderSnapshot(payload.snapshot);
  } catch (_error) {
    setJoinStatus("Error de red al enviar jugada.");
  } finally {
    state.selected = [];
    renderSnapshot(state.snapshot);
  }
}

function onBoardClick(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (!target.classList.contains("cell")) return;

  const row = Number(target.dataset.row);
  const col = Number(target.dataset.col);
  if (Number.isNaN(row) || Number.isNaN(col)) return;

  if (state.selected.find((p) => p.row === row && p.col === col)) return;
  state.selected.push({ row, col });

  if (state.selected.length > 2) {
    state.selected = state.selected.slice(-2);
  }

  renderSnapshot(state.snapshot);

  if (state.selected.length === 2) {
    void playSelected();
  }
}

function connectEvents() {
  const source = new EventSource("/events");
  source.addEventListener("game_update", (event) => {
    const payload = JSON.parse(event.data);
    if (payload.event) {
      state.events.push(payload.event);
      renderEvents();
    }
    if (payload.snapshot) {
      renderSnapshot(payload.snapshot);
    }
  });

  source.onerror = () => {
    source.close();
    setTimeout(connectEvents, 1500);
  };
}

function init() {
  if (state.playerName) {
    el.nameInput.value = state.playerName;
    setJoinStatus(`Sesion local detectada para ${state.playerName}. Si falla, vuelve a unirte.`);
  }

  renderSnapshot(state.snapshot);
  renderEvents();
  connectEvents();

  el.joinBtn.addEventListener("click", () => {
    void joinGame();
  });
  el.board.addEventListener("click", onBoardClick);
}

init();
