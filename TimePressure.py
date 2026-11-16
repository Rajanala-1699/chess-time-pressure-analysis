import requests, json, time
from datetime import datetime, date
from zoneinfo import ZoneInfo
from collections import Counter
from datetime import datetime

USER = "hikaru"
DAY = date(2026, 11, 8)                 # the Sunday you want
TZ = ZoneInfo("America/Chicago")        # change if you prefer UTC
HEADERS = {"User-Agent": "chess-project (your_email@example.com)"}

# Day boundaries as Unix timestamps
start = datetime(DAY.year, DAY.month, DAY.day, tzinfo=TZ).timestamp()
end = start + 24 * 3600

url = f"https://api.chess.com/pub/player/{USER}/games/{DAY.year}/{DAY.month:02d}"
r = requests.get(url, headers=HEADERS, timeout=30)
r.raise_for_status()
games = r.json()["games"]

day_games = [
    g for g in games
    if start <= g["end_time"] < end and g.get("rules") == "chess" and "pgn" in g
]
print(len(day_games), "games on", DAY)

# Save as JSON and as a PGN file
with open("hikaru_sunday.json", "w") as f:
    json.dump(day_games, f)

with open("hikaru_sunday.pgn", "w") as f:
    for g in day_games:
        f.write(g["pgn"] + "\n\n")

print(Counter(g["time_class"] for g in day_games))

# games per day for the whole month (Central time)
per_day = Counter(
    datetime.fromtimestamp(g["end_time"], TZ).date()
    for g in games
    if g.get("rules") == "chess"
)
for d, n in sorted(per_day.items()):
    print(d, n)


all_games = [g for g in games if g.get("rules") == "chess" and "pgn" in g]
print(len(all_games), "games")

with open("hikaru_nov.pgn", "w", encoding="utf-8") as f:
    for g in all_games:
        f.write(g["pgn"] + "\n\n")