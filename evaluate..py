import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import chess, chess.engine
import pandas as pd

STOCKFISH_PATH = r"C:\Users\likhi\OneDrive\Desktop\Common\Chess Project\stockfish-windows-x86-64-universal\stockfish\stockfish-windows-x86-64-universal.exe"
DEPTH = 10
GAMES_TO_EVAL = None          # start tiny to test; set to None for all games
BLUNDER_CP = 200           # eval drop (centipawns) that counts as a blunder

df = pd.read_csv("moves.csv")
game_ids = df["game_id"].unique()
if GAMES_TO_EVAL:
    game_ids = game_ids[:GAMES_TO_EVAL]
df = df[df["game_id"].isin(game_ids)].copy()

engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
engine.configure({"Threads": 4, "Hash": 256})

def evaluate(fen):
    board = chess.Board(fen)
    info = engine.analyse(board, chess.engine.Limit(depth=DEPTH))
    cp = info["score"].relative.score(mate_score=1000)   # from side-to-move's view
    return max(-1000, min(1000, cp))                     # cap extreme scores

out = []
for i, gid in enumerate(game_ids, 1):
    g = df[df["game_id"] == gid].sort_values("ply").copy()
    evals = [evaluate(f) for f in g["fen_before"]]
    g["eval_before"] = evals                              # mover's view before the move
    nxt = pd.Series(evals).shift(-1).values               # opponent's view after the move
    g["eval_after"] = -nxt                                # convert to mover's view
    g["eval_drop"] = g["eval_before"] - g["eval_after"]
    out.append(g.dropna(subset=["eval_after"]))           # last move of each game has no "after"
    print(f"game {i}/{len(game_ids)} done")

engine.quit()

res = pd.concat(out)
res["is_blunder"] = (res["eval_drop"] >= BLUNDER_CP).astype(int)
res.to_csv("moves_eval.csv", index=False)

print(f"{len(res)} moves, blunder rate = {res['is_blunder'].mean():.2%}")