import io, os, shutil, time
import requests
import numpy as np
import pandas as pd
import streamlit as st
import chess, chess.pgn, chess.engine

CONTACT = os.environ.get("CONTACT_EMAIL", "contact-not-set")
HEADERS = {"User-Agent": f"chess-time-pressure-app (contact: {CONTACT})"}
LOCAL_SF = r"C:\Users\likhi\OneDrive\Desktop\Common\Chess Project\stockfish-windows-x86-64-universal\stockfish\stockfish-windows-x86-64-universal.exe"

def find_stockfish():
    for p in [os.environ.get("STOCKFISH_PATH"), shutil.which("stockfish"), "/usr/games/stockfish", LOCAL_SF]:
        if p and os.path.exists(p):
            return p
    return None

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_games(user, time_class, n_months):
    r = requests.get(f"https://api.chess.com/pub/player/{user}/games/archives", headers=HEADERS, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    games = []
    for url in reversed(r.json()["archives"][-n_months:]):
        data = requests.get(url, headers=HEADERS, timeout=30).json().get("games", [])
        games += [g for g in data if g.get("rules") == "chess"
                  and g.get("time_class") == time_class and "pgn" in g]
        time.sleep(0.5)
    return sorted(games, key=lambda g: g["end_time"], reverse=True)

def parse_game(pgn_text, user):
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    h = game.headers
    tc = h.get("TimeControl", "")
    try:
        base, inc = (int(x) for x in tc.split("+")) if "+" in tc else (int(tc), 0)
    except ValueError:
        return []
    gid = h.get("Link", "")
    clocks = {chess.WHITE: float(base), chess.BLACK: float(base)}
    rows, node, ply = [], game, 0
    while node.variations:
        nxt = node.variation(0)
        mover = node.board().turn
        clk = nxt.clock()
        if clk is None:
            return []
        name = h["White"] if mover == chess.WHITE else h["Black"]
        rows.append({
            "game_id": gid, "ply": ply, "move_number": ply // 2 + 1,
            "who": "Player" if name.lower() == user.lower() else "Opponents",
            "inc_sec": inc, "base_sec": base,
            "clock_before": clocks[mover],
            "time_spent": clocks[mover] + inc - clk,
            "fen_before": node.board().fen(),
        })
        clocks[mover] = clk
        node, ply = nxt, ply + 1
    return rows

def evaluate_game(rows, engine, depth, blunder_cp):
    df = pd.DataFrame(rows)
    df = df[df["ply"] >= 10].copy()          # skip opening moves (saves time)
    if len(df) < 3:
        return None
    evals = []
    for fen in df["fen_before"]:
        info = engine.analyse(chess.Board(fen), chess.engine.Limit(depth=depth))
        evals.append(max(-1000, min(1000, info["score"].relative.score(mate_score=1000))))
    df["eval_before"] = evals
    df["eval_after"] = -pd.Series(evals, index=df.index).shift(-1)
    df["eval_drop"] = df["eval_before"] - df["eval_after"]
    df = df.dropna(subset=["eval_after"])
    df["is_blunder"] = (df["eval_drop"] >= blunder_cp).astype(int)
    return df

# ------------------------------ UI ------------------------------
st.set_page_config(page_title="Chess Time Pressure Analyzer", layout="wide")
st.title("Chess Time Pressure Analyzer")
st.caption("How does the clock affect your mistakes? Enter any Chess.com username.")

with st.sidebar:
    username = st.text_input("Chess.com username", "hikaru").strip().lower()
    time_class = st.selectbox("Time control", ["blitz", "bullet", "rapid"])
    n_months = st.slider("Months to look back", 1, 6, 2)
    max_games = st.slider("Max games to analyze", 10, 100, 30, step=5)
    depth = st.slider("Stockfish depth", 8, 14, 10)
    blunder_cp = st.select_slider("Blunder threshold (centipawns)", [100, 150, 200, 300], value=200)
    run = st.button("Analyze", type="primary")

if run:
    sf = find_stockfish()
    if not sf:
        st.error("Stockfish not found. Set the STOCKFISH_PATH environment variable or edit LOCAL_SF.")
        st.stop()
    games = fetch_games(username, time_class, n_months)
    if games is None:
        st.error(f"User '{username}' not found on Chess.com.")
        st.stop()
    if not games:
        st.warning(f"No {time_class} games found in the last {n_months} month(s).")
        st.stop()
    games = games[:max_games]
    st.write(f"Analyzing {len(games)} {time_class} games for **{username}**...")

    engine = chess.engine.SimpleEngine.popen_uci(sf)
    engine.configure({"Threads": 2, "Hash": 128})
    bar, parts = st.progress(0.0), []
    for i, g in enumerate(games, 1):
        rows = parse_game(g["pgn"], username)
        if rows:
            res = evaluate_game(rows, engine, depth, blunder_cp)
            if res is not None:
                parts.append(res)
        bar.progress(i / len(games))
    engine.quit()
    if not parts:
        st.warning("No games with usable clock data.")
        st.stop()
    st.session_state["df"] = pd.concat(parts)
    st.session_state["meta"] = (username, time_class)

if "df" in st.session_state:
    df = st.session_state["df"].copy()
    username, time_class = st.session_state["meta"]
    df = df[(df["eval_before"].abs() <= 300) & (df["move_number"] > 5)]

    df["Clock left"] = pd.cut(df["clock_before"], [-1, 10, 30, 60, 120, 1000],
                              labels=["0-10s", "10-30s", "30-60s", "60-120s", "120s+"])
    df["Phase"] = pd.cut(df["move_number"], [5, 15, 30, 500],
                         labels=["Moves 6-15", "Moves 16-30", "Moves 31+"])

    st.subheader(f"Results for {username} ({time_class}) | {df['game_id'].nunique()} games, {len(df):,} moves")

    summary = (df.groupby(["Clock left", "who"], observed=True)
                 .agg(moves=("is_blunder", "size"), blunders=("is_blunder", "sum"),
                      blunder_pct=("is_blunder", lambda s: round(s.mean() * 100, 2)),
                      avg_eval_drop=("eval_drop", lambda s: round(s.mean(), 1)))
                 .reset_index())

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Blunder rate (%) by time left**")
        st.bar_chart(summary.pivot(index="Clock left", columns="who", values="blunder_pct"))
    with c2:
        st.markdown("**Average eval drop (centipawns) by time left**")
        st.bar_chart(summary.pivot(index="Clock left", columns="who", values="avg_eval_drop"))

    c3, c4 = st.columns(2)
    with c3:
        st.markdown("**Median seconds spent per move**")
        spent = df.groupby(["Clock left", "who"], observed=True)["time_spent"].median().unstack()
        st.bar_chart(spent)
    with c4:
        st.markdown("**Blunder rate (%) by game phase**")
        ph = df.groupby(["Phase", "who"], observed=True)["is_blunder"].mean().mul(100).unstack()
        st.bar_chart(ph)

    st.markdown("**Summary table** (buckets with under ~30 blunders are noisy)")
    st.dataframe(summary, use_container_width=True)

    try:
        import statsmodels.formula.api as smf
        p = df[df["who"] == "Player"].copy()
        p["log_clock"] = np.log(p["clock_before"] + 1)
        if p["is_blunder"].sum() >= 20:
            m = smf.logit("is_blunder ~ log_clock + move_number", data=p).fit(disp=0)
            coef, pv = m.params["log_clock"], m.pvalues["log_clock"]
            st.info(f"Logistic regression (player's moves): log_clock coefficient = {coef:.3f}, "
                    f"p = {pv:.3g}. Negative = more time means fewer blunders.")
        else:
            st.info("Too few blunders for a reliable regression. Analyze more games.")
    except Exception as e:
        st.caption(f"Regression skipped: {e}")

    st.download_button("Download move data (CSV)",
                       df.drop(columns=["fen_before"]).to_csv(index=False),
                       file_name=f"{username}_{time_class}_moves.csv")