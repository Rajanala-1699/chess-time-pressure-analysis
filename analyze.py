import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import duckdb
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import statsmodels.formula.api as smf

df = pd.read_csv("moves_eval.csv")

# Clock BEFORE the move (what the player had when they started thinking)
df["clock_before"] = df["clock_after"] + df["time_spent"] - df["inc_sec"]
df["is_hikaru"] = (df["player"].str.lower() == "hikaru").astype(int)
df["who"] = np.where(df["is_hikaru"] == 1, "Hikaru", "Opponents")

# Keep positions that aren't already decided, and skip the first moves (premoves/opening book)
df = df[(df["eval_before"].abs() <= 300) & (df["move_number"] > 5)].copy()

# ---------- SQL (DuckDB) ----------
query = """
SELECT
SUM(is_blunder)               AS blunders,
  who,
  CASE WHEN clock_before < 10  THEN '1) 0-10s'
       WHEN clock_before < 30  THEN '2) 10-30s'
       WHEN clock_before < 60  THEN '3) 30-60s'
       WHEN clock_before < 120 THEN '4) 60-120s'
       ELSE '5) 120s+' END AS clock_bucket,
  COUNT(*)                      AS moves,
  ROUND(AVG(is_blunder) * 100, 2) AS blunder_pct,
  ROUND(AVG(eval_drop), 1)      AS avg_eval_drop
FROM df
GROUP BY who, clock_bucket
ORDER BY who, clock_bucket
"""
summary = duckdb.sql(query).df()
print(summary)
summary.to_csv("summary_by_clock.csv", index=False)

# ---------- Chart ----------
pivot = summary.pivot(index="clock_bucket", columns="who", values="blunder_pct")
pivot.plot(kind="bar", figsize=(8, 5))
plt.title("Blunder rate by time remaining (Hikaru blitz, Nov 2026)")
plt.ylabel("Blunder rate (%)")
plt.xlabel("Seconds on clock before the move")
plt.xticks(rotation=0)
plt.tight_layout()
plt.savefig("blunder_by_clock.png", dpi=150)
plt.show()

# ---------- Regression ----------
df["log_clock"] = np.log(df["clock_before"] + 1)
model = smf.logit("is_blunder ~ log_clock + move_number + is_hikaru", data=df).fit(disp=0)
print(model.summary())


# ---------- Chart 2: time spent per move vs clock remaining ----------
df["clock_bucket"] = pd.cut(df["clock_before"], [0, 10, 30, 60, 120, 400],
                            labels=["0-10s", "10-30s", "30-60s", "60-120s", "120s+"])
spent = df.groupby(["clock_bucket", "who"], observed=True)["time_spent"].median().unstack()
spent.plot(kind="bar", figsize=(8, 5))
plt.title("Median time spent per move by clock remaining")
plt.ylabel("Seconds spent on the move")
plt.xlabel("Seconds on clock before the move")
plt.xticks(rotation=0)
plt.tight_layout()
plt.savefig("time_spent_by_clock.png", dpi=150)
plt.show()

# ---------- Chart 3: blunder rate by game phase ----------
df["phase"] = pd.cut(df["move_number"], [5, 15, 30, 500],
                     labels=["Moves 6-15", "Moves 16-30", "Moves 31+"])
phase = df.groupby(["phase", "who"], observed=True)["is_blunder"].mean().mul(100).unstack()
phase.plot(kind="bar", figsize=(8, 5))
plt.title("Blunder rate by game phase")
plt.ylabel("Blunder rate (%)")
plt.xticks(rotation=0)
plt.tight_layout()
plt.savefig("blunder_by_phase.png", dpi=150)
plt.show()

# ---------- Regression summary ----------
coef = model.params["log_clock"]
p = model.pvalues["log_clock"]
print(f"log_clock coefficient = {coef:.3f}, p = {p:.4g}, odds ratio per 1 log-unit = {np.exp(coef):.2f}")