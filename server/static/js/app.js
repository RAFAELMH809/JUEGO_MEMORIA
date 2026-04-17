const state = {
  snapshot: window.__INITIAL_SNAPSHOT__ || null,
  events: [],
  ranking: [],
  history: []
};

const el = {
  gameStatus: document.getElementById("game-status"),
  connectedPlayers: document.getElementById("connected-players"),
  currentTurn: document.getElementById("current-turn"),
  remainingPairs: document.getElementById("remaining-pairs"),
  board: document.getElementById("board"),
  playersTbody: document.getElementById("players-tbody"),
  rankingTbody: document.getElementById("ranking-tbody"),
  eventLog: document.getElementById("event-log"),
  matchesTbody: document.getElementById("matches-tbody"),
  winnerBanner: document.getElementById("winner-banner")
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
        <strong>${ev.event_type}</strong>
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
        <td>${m.status}</td>
      </tr>
    `
    )
    .join("");
}

function renderSnapshot(snapshot) {
  if (!snapshot) return;
  state.snapshot = snapshot;

  el.gameStatus.textContent = snapshot.status;
  el.connectedPlayers.textContent = snapshot.connected_players;
  el.currentTurn.textContent = snapshot.current_turn_player_name || "N/A";
  el.remainingPairs.textContent = snapshot.remaining_pairs;

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

(async function init() {
  renderSnapshot(state.snapshot);
  await refreshStatsAndHistory();
  connectEvents();
  setInterval(refreshStatsAndHistory, 5000);
})();
