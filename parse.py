import chess.pgn
import pandas as pd


N_GAMES = None          # set to None to parse every game
PGN_FILE = "hikaru_nov.pgn"

def parse_tc(tc):
    """'180+2' -> (180, 2); '300' -> (300, 0)"""
    if "+" in tc:
        base, inc = tc.split("+")
        return int(base), int(inc)
    return int(tc), 0

rows = []
count = 0

with open(PGN_FILE, encoding="utf-8") as f:
    while True:
        game = chess.pgn.read_game(f)
        if game is None:
            break
        if N_GAMES and count >= N_GAMES:
            break

        h = game.headers
        try:
            base, inc = parse_tc(h["TimeControl"])
        except (KeyError, ValueError):
            continue                      # skip daily/odd formats

        count += 1
        game_id = h.get("Link", f"game_{count}")
        clocks = {chess.WHITE: float(base), chess.BLACK: float(base)}

        node, ply = game, 0
        while node.variations:
            nxt = node.variation(0)
            mover = node.board().turn     # side making this move
            clk = nxt.clock()
            if clk is None:               # game has no clock data
                break

            rows.append({
                "game_id": game_id,
                "ply": ply,
                "move_number": ply // 2 + 1,
                "color": "white" if mover == chess.WHITE else "black",
                "player": h["White"] if mover == chess.WHITE else h["Black"],
                "player_elo": h.get("WhiteElo" if mover == chess.WHITE else "BlackElo"),
                "opp_elo": h.get("BlackElo" if mover == chess.WHITE else "WhiteElo"),
                "base_sec": base,
                "inc_sec": inc,
                "clock_after": clk,
                "opp_clock": clocks[not mover],
                "time_spent": clocks[mover] + inc - clk,
                "fen_before": node.board().fen(),
                "move_uci": nxt.move.uci(),
            })

            clocks[mover] = clk
            node, ply = nxt, ply + 1

df = pd.DataFrame(rows)
df.to_csv("moves.csv", index=False)

print(f"{count} games, {len(df)} moves saved to moves.csv")
print(df[["clock_after", "opp_clock", "time_spent"]].describe())

df = pd.read_csv("moves.csv")
print(df[["game_id", "move_number", "color", "clock_after", "opp_clock", "time_spent"]].head(20))