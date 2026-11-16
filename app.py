import altair as alt
import duckdb
import io, os, shutil, time
import requests
import numpy as np
import pandas as pd
import streamlit as st
import chess, chess.pgn, chess.engine

CONTACT = os.environ.get("CONTACT_EMAIL", "contact-not-set")
HEADERS = {"User-Agent": f"chess-time-pressure-app (contact: {CONTACT})"}
LOCAL_SF = r"C:\Users\likhi\OneDrive\Desktop\Common\Chess Project\stockfish-windows-x86-64-universal\stockfish\stockfish-windows-x86-64-universal.exe"

AMBER, SLATE = "#f59e0b", "#7c8aa0"
GREEN, YELLOW, RED = "#34d399", "#fbbf24", "#f87171"

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

# ------------------------------ Styling ------------------------------
st.set_page_config(page_title="Chess Time Pressure Analyzer", page_icon="♟️", layout="centered")

st.markdown("""
<style>
.block-container {padding-top: 2rem; max-width: 1000px;}
.hero {background: linear-gradient(135deg, #243142 0%, #0f1419 70%);
       border: 1px solid #2a3643; border-radius: 18px; padding: 30px 34px; margin-bottom: 22px;}
.hero h1 {margin: 0; font-size: 2.2rem; line-height: 1.15;}
.hero p {margin: 10px 0 0; color: #9fb0c0; font-size: 1.08rem;}
.card {background: #1a222c; border: 1px solid #2a3643; border-top: 4px solid var(--c);
       border-radius: 14px; padding: 18px 20px; height: 100%;}
.card .label {color: #9fb0c0; font-size: .8rem; text-transform: uppercase; letter-spacing: .07em;}
.card .big {font-size: 2.5rem; font-weight: 700; line-height: 1.1; margin: 8px 0 6px; color: var(--c);}
.card .sub {color: #b8c4d0; font-size: .95rem;}
.verdict {border-left: 5px solid var(--c); background: #1a222c; border-radius: 10px;
          padding: 14px 18px; margin: 16px 0 6px; font-size: 1.05rem;}
.small {color: #8a9bab; font-size: .9rem;}
div[data-testid="stForm"] {border: 1px solid #2a3643; border-radius: 14px; padding: 18px;}
h3 {margin-top: 1.6rem !important;}
</style>
""", unsafe_allow_html=True)

def card(label, big, sub, color):
    return (f'<div class="card" style="--c:{color}"><div class="label">{label}</div>'
            f'<div class="big">{big}</div><div class="sub">{sub}</div></div>')

st.markdown("""
<div class="hero">
  <h1>♟️ Chess Time Pressure Analyzer</h1>
  <p>Does running low on the clock make you play worse? Enter any Chess.com username
     and find out, with a chess engine checking every move.</p>
</div>
""", unsafe_allow_html=True)

# ------------------------------ Inputs (main page, works on phones) ------------------------------
with st.form("controls"):
    c1, c2, c3 = st.columns([2, 1.2, 1.2])
    username = c1.text_input("Chess.com username", "hikaru", help="Try: hikaru, magnuscarlsen, or your own").strip().lower()
    time_class = c2.selectbox("Game speed", ["blitz", "bullet", "rapid"],
                              help="Bullet is about 1-2 minutes per player, blitz 3-5, rapid 10+.")
    max_games = c3.slider("Games to analyze", 10, 100, 30, step=5,
                          help="More games gives more reliable results but takes longer.")
    with st.expander("Advanced settings"):
        n_months = st.slider("How far back to look (months)", 1, 6, 2)
        depth = st.slider("Analysis strength", 8, 14, 10, help="Higher is more accurate but slower.")
        blunder_cp = st.select_slider("What counts as a big mistake?", [100, 150, 200, 300], value=200,
                                      format_func=lambda x: f"{x/100:g}+ pawns lost")
    run = st.form_submit_button("Analyze", type="primary", use_container_width=True)

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

    engine = chess.engine.SimpleEngine.popen_uci(sf)
    engine.configure({"Threads": 2, "Hash": 128})
    bar, parts = st.progress(0.0, text="Starting..."), []
    for i, g in enumerate(games, 1):
        rows = parse_game(g["pgn"], username)
        if rows:
            res = evaluate_game(rows, engine, depth, blunder_cp)
            if res is not None:
                parts.append(res)
        bar.progress(i / len(games), text=f"Checking game {i} of {len(games)} with the chess engine...")
    engine.quit()
    bar.empty()
    if not parts:
        st.warning("No games with usable clock data.")
        st.stop()
    st.session_state["df"] = pd.concat(parts)
    st.session_state["meta"] = (username, time_class, blunder_cp)

ORDER = ["120s+", "60-120s", "30-60s", "10-30s", "0-10s"]
BUCKET = """CASE WHEN clock_before < 10  THEN '0-10s'
                 WHEN clock_before < 30  THEN '10-30s'
                 WHEN clock_before < 60  THEN '30-60s'
                 WHEN clock_before < 120 THEN '60-120s'
                 ELSE '120s+' END"""

# ------------------------------ Results ------------------------------
if "df" in st.session_state:
    df = st.session_state["df"].copy()
    username, time_class, blunder_cp = st.session_state["meta"]
    df = df[(df["eval_before"].abs() <= 300) & (df["move_number"] > 5)].copy()
    if "move" not in df.columns:
        df["move"] = ""
    df["player"] = df["who"].map({"Player": username, "Opponents": "Opponents"})
    me = df[df["who"] == "Player"]

    con = duckdb.connect()
    con.register("moves", df)

    st.markdown(f"## Results for {username}")
    st.markdown(f'<p class="small">Based on {df["game_id"].nunique()} {time_class} games '
                f'({len(df):,} moves checked). A <b>big mistake</b> is a move where the computer says '
                f'the player gave away about {blunder_cp / 100:g} pawns of advantage or more '
                f'(a knight or bishop is worth about 3 pawns).</p>', unsafe_allow_html=True)

    # ---------- Short answer cards ----------
    sa = con.execute("""
        SELECT CASE WHEN clock_before >= 60 THEN 1
                    WHEN clock_before >= 30 THEN 2
                    ELSE 3 END           AS grp,
               COUNT(*)                  AS moves,
               100.0 * AVG(is_blunder)   AS rate
        FROM moves
        WHERE who = 'Player'
        GROUP BY grp
        ORDER BY grp
    """).df().set_index("grp")

    specs = [(1, "Plenty of time", "60+ seconds left", GREEN),
             (2, "Getting short", "30-60 seconds left", YELLOW),
             (3, "Rushed", "under 30 seconds left", RED)]
    rates = []
    for col, (g, title, sub, color) in zip(st.columns(3), specs):
        with col:
            if g in sa.index and sa.loc[g, "moves"] >= 30:
                r = float(sa.loc[g, "rate"])
                rates.append(r)
                note = f"about 1 in {round(100 / r)} moves is a big mistake" if r > 0 else "no big mistakes found"
                st.markdown(card(f"{title} · {sub}", f"{r:.1f}%", note, color), unsafe_allow_html=True)
            else:
                rates.append(None)
                st.markdown(card(f"{title} · {sub}", "n/a", "not enough moves, try more games", "#64748b"),
                            unsafe_allow_html=True)

    if rates[0] is not None and rates[2] is not None:
        if rates[0] == 0 and rates[2] > 0:
            v, vc = f"<b>Time pressure hurts {username}.</b> Big mistakes only showed up when they were rushed.", RED
        elif rates[0] > 0:
            ratio = rates[2] / rates[0]
            if ratio >= 1.5:
                v, vc = (f"<b>Time pressure hurts {username}.</b> When rushed, they make about "
                         f"<b>{ratio:.1f}x</b> as many big mistakes as when they have plenty of time."), RED
            elif ratio >= 0.8:
                v, vc = f"<b>Time pressure doesn't change {username}'s accuracy much</b> in this sample.", GREEN
            else:
                v, vc = (f"<b>Surprisingly, {username} made fewer big mistakes when rushed</b> in this sample. "
                         "With few games this can be random chance."), YELLOW
        else:
            v, vc = "No clear pattern in this sample.", "#64748b"
        st.markdown(f'<div class="verdict" style="--c:{vc}">{v}</div>', unsafe_allow_html=True)

    tab1, tab2, tab3, tab4 = st.tabs(["📊 Mistakes vs. time", "🔥 Biggest mistakes", "⏱ Thinking time", "🔬 Details"])

    # ---------- Tab 1: main chart ----------
    with tab1:
        MAIN_QUERY = f"""
SELECT player                                   AS Who,
       {BUCKET}                                 AS TimeLeft,
       COUNT(*)                                 AS Moves,
       CAST(SUM(is_blunder) AS INTEGER)         AS Mistakes,
       ROUND(100.0 * AVG(is_blunder), 2)        AS Per100
FROM moves
GROUP BY 1, 2
"""
        cd = con.execute(MAIN_QUERY).df()
        chart = (alt.Chart(cd).mark_line(point=alt.OverlayMarkDef(size=90, filled=True), strokeWidth=3).encode(
                    x=alt.X("TimeLeft:N", sort=ORDER, title="Time left on the clock before the move",
                            axis=alt.Axis(labelAngle=0)),
                    y=alt.Y("Per100:Q", title="Big mistakes per 100 moves"),
                    color=alt.Color("Who:N", title=None,
                                    scale=alt.Scale(domain=[username, "Opponents"], range=[AMBER, SLATE])),
                    tooltip=[alt.Tooltip("Who:N", title="Who"),
                             alt.Tooltip("TimeLeft:N", title="Time left"),
                             alt.Tooltip("Moves:Q", title="Moves in this group"),
                             alt.Tooltip("Mistakes:Q", title="Big mistakes"),
                             alt.Tooltip("Per100:Q", title="Per 100 moves")])
                 .properties(height=360))
        st.altair_chart(chart, use_container_width=True)
        st.caption("**How to read it:** each dot is a group of moves made with that much time left. "
                   "Left = lots of time, right = almost none. A line that climbs toward the right means "
                   "more big mistakes when rushed. Hover over a dot to see how many moves it's based on, "
                   "and don't trust dots built from very few moves.")
        with st.expander("Show the SQL behind this chart"):
            st.code(MAIN_QUERY.strip(), language="sql")

    # ---------- Tab 2: worst mistakes ----------
    with tab2:
        show = con.execute("""
            SELECT game_id                                   AS Game,
                   move_number                               AS "Move number",
                   move                                      AS "Move played",
                   CAST(ROUND(clock_before) AS INTEGER)      AS "Seconds left",
                   ROUND(eval_drop / 100.0, 1)               AS "Pawns of advantage lost"
            FROM moves
            WHERE who = 'Player' AND is_blunder = 1
            ORDER BY eval_drop DESC
            LIMIT 8
        """).df()
        if len(show):
            st.dataframe(show, hide_index=True, use_container_width=True,
                         column_config={"Game": st.column_config.LinkColumn("Game", display_text="Open on Chess.com")})
            st.caption("Look at the **Seconds left** column. Mistakes with only a few seconds left were probably "
                       "caused by time pressure. Mistakes with lots of time left were just bad moves.")
        else:
            st.write("No big mistakes found in these games.")

    # ---------- Tab 3: thinking time ----------
    with tab3:
        think = con.execute(f"""
            SELECT {BUCKET} AS TimeLeft, MEDIAN(time_spent) AS Seconds
            FROM moves
            WHERE who = 'Player'
            GROUP BY 1
        """).df()
        bars = (alt.Chart(think).mark_bar(color=AMBER, cornerRadiusTopLeft=5, cornerRadiusTopRight=5).encode(
                    x=alt.X("TimeLeft:N", sort=ORDER, title="Time left on the clock before the move",
                            axis=alt.Axis(labelAngle=0)),
                    y=alt.Y("Seconds:Q", title="Typical seconds spent on one move"))
                .properties(height=320))
        st.altair_chart(bars, use_container_width=True)
        st.caption("Bars that shrink toward the right mean the player moves faster when low on time, "
                   "which is normal. Fast moves are where most time-pressure mistakes happen.")

    # ---------- Tab 4: details ----------
    with tab4:
        st.markdown("**Big mistakes by stage of the game (% of moves)**")
        ph = con.execute("""
            SELECT CASE WHEN move_number <= 15 THEN 'Opening (moves 6-15)'
                        WHEN move_number <= 30 THEN 'Middlegame (16-30)'
                        ELSE 'Endgame (31+)' END      AS Phase,
                   player                             AS Who,
                   ROUND(100.0 * AVG(is_blunder), 2)  AS Rate
            FROM moves
            GROUP BY 1, 2
        """).df().pivot(index="Phase", columns="Who", values="Rate")
        ph = ph.reindex(["Opening (moves 6-15)", "Middlegame (16-30)", "Endgame (31+)"])
        cols = [c for c in [username, "Opponents"] if c in ph.columns]
        st.bar_chart(ph[cols], color=[AMBER, SLATE][:len(cols)])
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
    s1, s2, s3 = st.columns(3)
    s1.markdown(card("Step 1", "Pick a player", "Type any Chess.com username above.", AMBER), unsafe_allow_html=True)
    s2.markdown(card("Step 2", "Hit Analyze", "A chess engine checks every move. Takes a few minutes.", YELLOW), unsafe_allow_html=True)
    s3.markdown(card("Step 3", "Read the verdict", "See how mistakes change as the clock runs down.", GREEN), unsafe_allow_html=True)

st.markdown('<p class="small" style="margin-top:2.5rem">Data from the Chess.com public API · '
            'Moves evaluated with Stockfish · Built with Python, DuckDB and Streamlit</p>',
            unsafe_allow_html=True)