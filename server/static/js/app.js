const state = {
  snapshot: globalThis.__INITIAL_SNAPSHOT__ || null,
  events: [],
  ranking: [],
  history: []
};

const el = {
  gameStatus: document.getElementById("game-status"),
  connectedPlayers: document.getElementById("connected-players"),
  currentTurn: document.getElementById("current-turn"),
  remainingPairs: document.getElementById("remaining-pairs"),
  minPlayers: document.getElementById("min-players"),
  maxPlayers: document.getElementById("max-players"),
  boardRecommendation: document.getElementById("board-recommendation"),
  board: document.getElementById("board"),
  playersTbody: document.getElementById("players-tbody"),
  rankingTbody: document.getElementById("ranking-tbody"),
  eventLog: document.getElementById("event-log"),
  matchesTbody: document.getElementById("matches-tbody"),
  winnerBanner: document.getElementById("winner-banner"),
  adminStartBtn: document.getElementById("admin-start-btn"),
  adminResetBtn: document.getElementById("admin-reset-btn"),
  adminActionStatus: document.getElementById("admin-action-status")
};

const statusLabelMap = {
  WAITING_FOR_PLAYERS: "Esperando jugadores",
  IN_PROGRESS: "En curso",
  FINISHED: "Finalizada"
};

const eventLabelMap = {
  PLAYER_JOINED: "Jugador unido",
  GAME_STARTED: "Partida iniciada",
  TURN_CHANGED: "Turno cambiado",
  TURN_PLAYED: "Jugada realizada",
  MATCH_FOUND: "Pareja encontrada",
  MISS_REVEALED: "Intento sin pareja",
  MISS_HIDDEN: "Cartas ocultadas",
  GAME_OVER: "Fin de partida",
  STATS_UPDATED: "Estadísticas actualizadas",
  FULL_STATE_SYNC: "Sincronización",
  SYSTEM_MESSAGE: "Sistema"
};

function shortId(id) {
  if (!id) return "";
  return id.slice(0, 8);
}

function renderBoard(board) {
  if (!board) return;
  el.board.style.gridTemplateColumns = `repeat(${board.cols}, minmax(28px, 1fr))`;
  el.board.innerHTML = board.cells
    .map((cell) => {
      const classes = ["card-cell"];
      if (cell.is_revealed) classes.push("revealed");
      if (cell.is_matched) classes.push("matched");
      return `<div class="${classes.join(" ")}" title="${cell.row},${cell.col}">${cell.value}</div>`;
    })
    .join("");
}

function renderPlayers(players) {
  const waiting = state.snapshot?.status === "WAITING_FOR_PLAYERS";
  el.playersTbody.innerHTML = players
    .map(
      (p) => `
      <tr>
        <td>${p.name}</td>
        <td>${shortId(p.player_id)}</td>
        <td>${p.score}</td>
        <td>${p.moves}</td>
        <td>${p.pairs_found}</td>
        <td>${Number(p.average_response_time).toFixed(3)}</td>
        <td>${Number(p.total_response_time).toFixed(3)}</td>
        <td>${p.is_current_turn ? "Si" : "No"}</td>
        <td>${waiting ? `<button class="btn btn-danger btn-sm" data-remove-player-id="${p.player_id}">Quitar</button>` : "-"}</td>
      </tr>
    `
    )
    .join("");
}

function renderRanking(ranking) {
  el.rankingTbody.innerHTML = ranking
    .map(
      (p, idx) => `
      <tr>
        <td>${idx + 1}</td>
        <td>${p.name}</td>
        <td>${p.score}</td>
        <td>${p.moves}</td>
        <td>${Number(p.average_response_time).toFixed(3)}</td>
      </tr>
    `
    )
    .join("");
}

function renderEvents(events) {
  const recent = events.slice(-40).reverse();
  el.eventLog.innerHTML = recent
    .map(
      (ev) => `
      <div class="event-item">
        <strong>${eventLabelMap[ev.event_type] || ev.event_type}</strong>
        <div>${ev.message}</div>
        <small>${ev.timestamp}</small>
      </div>
    `
    )
    .join("");
}

function renderMatchHistory(matches) {
  el.matchesTbody.innerHTML = matches
    .map(
      (m) => `
      <tr>
        <td>${shortId(m.match_id)}</td>
        <td>${m.started_at || "-"}</td>
        <td>${m.finished_at || "-"}</td>
        <td>${m.rows}x${m.cols}</td>
        <td>${(m.winners || []).join(", ") || "-"}</td>
        <td>${statusLabelMap[m.status] || m.status}</td>
      </tr>
    `
    )
    .join("");
}

function renderSnapshot(snapshot) {
  if (!snapshot) return;
  state.snapshot = snapshot;

  el.gameStatus.textContent = statusLabelMap[snapshot.status] || snapshot.status;
  el.connectedPlayers.textContent = snapshot.connected_players;
  el.currentTurn.textContent = snapshot.current_turn_player_name || "No aplica";
  el.remainingPairs.textContent = snapshot.remaining_pairs;
  el.minPlayers.textContent = snapshot.min_players;
  el.maxPlayers.textContent = snapshot.max_players || 4;
  el.boardRecommendation.textContent = snapshot.board_recommendation || `${snapshot.board.rows}x${snapshot.board.cols}`;

  renderBoard(snapshot.admin_board || snapshot.board);
  renderPlayers(snapshot.players || []);

  if (snapshot.game_over) {
    const winners = (snapshot.winners || []).join(", ") || "Sin ganador";
    el.winnerBanner.textContent = `Partida finalizada. Ganador(es): ${winners}`;
  } else {
    el.winnerBanner.textContent = "Partida en curso";
  }
}

async function refreshStatsAndHistory() {
  const [statsRes, historyRes] = await Promise.all([
    fetch("/api/stats"),
    fetch("/api/history?limit=20")
  ]);

  const stats = await statsRes.json();
  const history = await historyRes.json();

  state.ranking = stats.ranking || [];
  state.history = history.matches || [];

  renderRanking(state.ranking);
  renderMatchHistory(state.history);
  renderEvents(stats.recent_events || state.events);
}

function connectEvents() {
  const source = new EventSource("/events/admin");

  source.addEventListener("game_update", (event) => {
    const payload = JSON.parse(event.data);
    if (payload.event) {
      state.events.push(payload.event);
    }
    if (payload.snapshot) {
      renderSnapshot(payload.snapshot);
    }
    renderEvents(state.events);
  });

  source.onerror = () => {
    source.close();
    setTimeout(connectEvents, 1500);
  };
}

async function runAdminAction(url, successMessage) {
  try {
    const response = await fetch(url, { method: "POST" });
    if (!response.ok) {
      const payload = await response.json();
      el.adminActionStatus.textContent = payload.detail || "No se pudo ejecutar la accion";
      return;
    }

    const payload = await response.json();
    el.adminActionStatus.textContent = payload.reason || successMessage;
    if (payload.snapshot) {
      renderSnapshot(payload.snapshot);
    }
  } catch (error) {
    console.error("admin action failed", error);
    el.adminActionStatus.textContent = "Error de red al ejecutar accion admin";
  }
}

async function removePlayer(playerId) {
  try {
    const response = await fetch("/api/admin/remove-player", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ player_id: playerId })
    });

    if (!response.ok) {
      const payload = await response.json();
      el.adminActionStatus.textContent = payload.detail || "No se pudo quitar al jugador";
      return;
    }

    const payload = await response.json();
    el.adminActionStatus.textContent = payload.reason || "Jugador removido";
    if (payload.snapshot) {
      renderSnapshot(payload.snapshot);
    }
  } catch (error) {
    console.error("remove player failed", error);
    el.adminActionStatus.textContent = "Error de red al quitar jugador";
  }
}

function init() {
  renderSnapshot(state.snapshot);
  refreshStatsAndHistory().catch(() => {
    el.adminActionStatus.textContent = "No se pudo actualizar stats/historial";
  });
  connectEvents();
  setInterval(refreshStatsAndHistory, 5000);

  el.adminStartBtn.addEventListener("click", () => {
    void runAdminAction("/api/admin/start", "Partida iniciada");
  });
  el.adminResetBtn.addEventListener("click", () => {
    void runAdminAction("/api/admin/reset", "Partida reiniciada");
  });

  el.playersTbody.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const playerId = target.dataset.removePlayerId;
    if (!playerId) return;
    void removePlayer(playerId);
  });
}

init();
