import altair as alt
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
            "move": node.board().san(nxt.move),
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
st.caption("Find out whether running low on time makes a Chess.com player make more mistakes.")

with st.sidebar:
    st.header("Pick a player")
    username = st.text_input("Chess.com username", "hikaru").strip().lower()
    time_class = st.selectbox("Game speed", ["blitz", "bullet", "rapid"],
                              help="Bullet is about 1-2 minutes per player, blitz 3-5, rapid 10+.")
    max_games = st.slider("Number of games to analyze", 10, 100, 30, step=5,
                          help="More games gives more reliable results but takes longer.")
    n_months = st.slider("How far back to look (months)", 1, 6, 2)
    with st.expander("Advanced settings"):
        depth = st.slider("Analysis strength", 8, 14, 10,
                          help="Higher is more accurate but slower.")
        blunder_cp = st.select_slider("What counts as a big mistake?", [100, 150, 200, 300], value=200,
                                      format_func=lambda x: f"{x/100:g}+ pawns lost")
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
    st.session_state["meta"] = (username, time_class, blunder_cp)

ORDER = ["120s+", "60-120s", "30-60s", "10-30s", "0-10s"]

if "df" in st.session_state:
    df = st.session_state["df"].copy()
    username, time_class, blunder_cp = st.session_state["meta"]
    df = df[(df["eval_before"].abs() <= 300) & (df["move_number"] > 5)]
    if "move" not in df.columns:
        df["move"] = ""

    df["Who"] = df["who"].map({"Player": username, "Opponents": "Opponents"})
    df["TimeLeft"] = pd.cut(df["clock_before"], [-1, 10, 30, 60, 120, 100000],
                            labels=["0-10s", "10-30s", "30-60s", "60-120s", "120s+"]).astype(str)
    me = df[df["who"] == "Player"]

    st.subheader(f"Results for {username}")
    st.caption(f"Based on {df['game_id'].nunique()} {time_class} games ({len(df):,} moves analyzed).")
    st.markdown(f"**What's a \"big mistake\"?** A move where the computer says the player threw away "
                f"about {blunder_cp / 100:g} pawns of advantage or more. (A knight or bishop is worth about 3 pawns.)")

    # ---------- 1. Short answer ----------
    st.markdown("### 1. The short answer")
    groups = [("Plenty of time", "60+ sec left", me[me["clock_before"] >= 60]),
              ("Getting short", "30-60 sec left", me[(me["clock_before"] >= 30) & (me["clock_before"] < 60)]),
              ("Rushed", "under 30 sec left", me[me["clock_before"] < 30])]
    rates = []
    for col, (title, sub, d) in zip(st.columns(3), groups):
        with col:
            if len(d) >= 30:
                r = d["is_blunder"].mean() * 100
                rates.append(r)
                st.metric(f"{title} ({sub})", f"{r:.1f}% of moves")
                st.caption(f"about 1 in {round(100 / r)} moves is a big mistake" if r > 0
                           else "no big mistakes found")
            else:
                rates.append(None)
                st.metric(f"{title} ({sub})", "not enough data")
                st.caption("Try analyzing more games.")

    if rates[0] is not None and rates[2] is not None:
        if rates[0] == 0 and rates[2] > 0:
            st.warning(f"**Time pressure hurts {username}.** Big mistakes only showed up when they were rushed.")
        elif rates[0] > 0:
            ratio = rates[2] / rates[0]
            if ratio >= 1.5:
                st.warning(f"**Time pressure hurts {username}.** When rushed, they make about "
                           f"{ratio:.1f}x as many big mistakes as when they have plenty of time.")
            elif ratio >= 0.8:
                st.info(f"**Time pressure doesn't change {username}'s accuracy much** in this sample.")
            else:
                st.info(f"**Surprisingly, {username} made fewer big mistakes when rushed** in this sample. "
                        "With few games this can be random chance.")

    # ---------- 2. Main chart ----------
    st.markdown("### 2. How mistakes change as the clock runs down")
    cd = (df.groupby(["TimeLeft", "Who"])
            .agg(Moves=("is_blunder", "size"), Mistakes=("is_blunder", "sum")).reset_index())
    cd["Per100"] = (cd["Mistakes"] / cd["Moves"] * 100).round(2)
    chart = (alt.Chart(cd).mark_line(point=True, strokeWidth=3).encode(
                x=alt.X("TimeLeft:N", sort=ORDER, title="Time left on the clock before the move"),
                y=alt.Y("Per100:Q", title="Big mistakes per 100 moves"),
                color=alt.Color("Who:N", title=None),
                tooltip=[alt.Tooltip("Who:N", title="Who"),
                         alt.Tooltip("TimeLeft:N", title="Time left"),
                         alt.Tooltip("Moves:Q", title="Moves in this group"),
                         alt.Tooltip("Mistakes:Q", title="Big mistakes"),
                         alt.Tooltip("Per100:Q", title="Per 100 moves")])
             .properties(height=350))
    st.altair_chart(chart, use_container_width=True)
    st.caption("**How to read it:** each dot is a group of moves made with that much time left. "
               "The left side is lots of time, the right side is almost none. A line that climbs toward "
               "the right means more big mistakes when rushed. Hover over a dot to see how many moves it's "
               "based on, and don't trust dots built from very few moves.")

    # ---------- 3. Worst mistakes ----------
    st.markdown(f"### 3. {username}'s biggest mistakes")
    worst = me[me["is_blunder"] == 1].sort_values("eval_drop", ascending=False).head(8)
    if len(worst):
        show = pd.DataFrame({
            "Game": worst["game_id"].values,
            "Move number": worst["move_number"].values,
            "Move played": worst["move"].values,
            "Seconds left": worst["clock_before"].round(0).astype(int).values,
            "Pawns of advantage lost": (worst["eval_drop"] / 100).round(1).values,
        })
        st.dataframe(show, hide_index=True, use_container_width=True,
                     column_config={"Game": st.column_config.LinkColumn("Game", display_text="Open on Chess.com")})
        st.caption("Look at the **Seconds left** column. Mistakes with only a few seconds left were probably "
                   "caused by time pressure. Mistakes with lots of time left were just bad moves.")
    else:
        st.write("No big mistakes found in these games.")

    # ---------- 4. Thinking time ----------
    st.markdown("### 4. Does the player speed up when time is short?")
    think = me.groupby("TimeLeft")["time_spent"].median().reindex(ORDER).dropna().reset_index()
    think.columns = ["TimeLeft", "Seconds"]
    bars = (alt.Chart(think).mark_bar().encode(
                x=alt.X("TimeLeft:N", sort=ORDER, title="Time left on the clock before the move"),
                y=alt.Y("Seconds:Q", title="Typical seconds spent on one move"))
            .properties(height=300))
    st.altair_chart(bars, use_container_width=True)
    st.caption("Bars that shrink toward the right mean the player moves faster when low on time, "
               "which is normal. Fast moves are where most time-pressure mistakes happen.")

    # ---------- More details ----------
    with st.expander("More details (for the curious)"):
        st.markdown("**Big mistakes by stage of the game (% of moves)**")
        df["Phase"] = pd.cut(df["move_number"], [5, 15, 30, 500],
                             labels=["Opening (moves 6-15)", "Middlegame (16-30)", "Endgame (31+)"])
        ph = df.groupby(["Phase", "Who"], observed=True)["is_blunder"].mean().mul(100).unstack()
        st.bar_chart(ph)
        st.caption("Shows whether mistakes cluster late in games, which is when clocks run low.")

        st.markdown("**Full numbers**")
        table = cd.rename(columns={"TimeLeft": "Time left", "Moves": "Moves analyzed",
                                   "Mistakes": "Big mistakes", "Per100": "Big mistakes per 100 moves"})
        st.dataframe(table, hide_index=True, use_container_width=True)
        st.caption("Rows with fewer than about 30 big mistakes are noisy.")

        try:
            import statsmodels.formula.api as smf
            p = me.copy()
            p["log_clock"] = np.log(p["clock_before"] + 1)
            if p["is_blunder"].sum() >= 20:
                m = smf.logit("is_blunder ~ log_clock + move_number", data=p).fit(disp=0)
                coef, pv = m.params["log_clock"], m.pvalues["log_clock"]
                if pv < 0.05 and coef < 0:
                    msg = "more time on the clock goes with fewer big mistakes, and this is unlikely to be chance."
                elif pv < 0.05:
                    msg = "more time on the clock goes with MORE big mistakes, which is unusual."
                else:
                    msg = "no reliable link between time left and big mistakes was found. There may not be enough data."
                st.markdown(f"**Statistical test:** {msg}")
                st.caption(f"Technical details: coefficient {coef:.3f}, p-value {pv:.3g}. "
                           "A p-value under 0.05 means the pattern is unlikely to be random.")
            else:
                st.caption("Too few big mistakes for a statistical test. Analyze more games.")
        except Exception as e:
            st.caption(f"Statistical test skipped: {e}")

    st.download_button("Download the move data (CSV)",
                       df.drop(columns=["fen_before"]).to_csv(index=False),
                       file_name=f"{username}_{time_class}_moves.csv")
else:
    st.info("**How to use this:** type a Chess.com username in the sidebar on the left and click **Analyze**. "
            "The app looks at their recent games and shows whether running low on time makes them play worse.")