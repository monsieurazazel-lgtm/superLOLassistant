
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
riot_dump_one_match.py
- Télécharge UN match (via matchId fourni, ou le dernier match classé d'un joueur).
- Sauvegarde JSON brut du match + de la timeline (si dispo).
- Extrait des CSVs lisibles: participants.csv, teams.csv, objectives.csv, timeline_events.csv.

Exemples:
  # 1) En passant la clé en argument (pratique Windows)
  python riot_dump_one_match.py --api-key RGAPI-XXXX \
      --region europe --platform euw1 --name ztheo17 --tag EUW --count 50

  # 2) Avec clé en variable d'env (Git Bash)
  export RIOT_API_KEY="RGAPI-XXXX"
  python riot_dump_one_match.py --region europe --platform euw1 --name ztheo17 --tag EUW

  # 3) Match précis (sans passer par le joueur)
  python riot_dump_one_match.py --api-key RGAPI-XXXX --region europe --match-id EUW1_6999999999
"""

from __future__ import annotations
import argparse, os, json, time, random
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

try:
    from riotwatcher import RiotWatcher, LolWatcher, ApiError
except Exception as e:
    raise SystemExit("riotwatcher n'est pas installé. Fais: pip install riotwatcher\n" + str(e))

DATA_DIR = Path("data_one_match")
DATA_DIR.mkdir(exist_ok=True)

# ------------------------------
# Utils
# ------------------------------
def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def df_to_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)

def flatten_participants(info: Dict) -> pd.DataFrame:
    cols_basic = [
        "participantId","teamId","win","summonerName","puuid","championName","champLevel",
        "teamPosition","role","lane","kills","deaths","assists","totalMinionsKilled","neutralMinionsKilled",
        "goldEarned","goldSpent","visionScore","timeCCingOthers","totalDamageDealt","totalDamageDealtToChampions",
        "magicDamageDealt","physicalDamageDealt","trueDamageDealt","damageDealtToObjectives","damageSelfMitigated",
        "totalHeal","totalHealsOnTeammates","totalDamageTaken","totalTimeSpentDead","wardsPlaced","wardsKilled"
    ]
    parts = info.get("participants", [])
    rows = []
    for p in parts:
        row = {k: p.get(k) for k in cols_basic}
        # items
        for i in range(7):
            row[f"item{i}"] = p.get(f"item{i}")
        # spells
        row["summoner1Id"] = p.get("summoner1Id")
        row["summoner2Id"] = p.get("summoner2Id")
        # runes/perks (on garde brut + une version plate minimale)
        perks = p.get("perks", {})
        row["perks_raw"] = json.dumps(perks, ensure_ascii=False)
        # un petit extract "style u.gg": style prim/sec + 6 perks si présents
        try:
            styles = perks.get("styles", [])
            if styles and len(styles) >= 2:
                prim = styles[0]; sec = styles[1]
                row["runes_primary_style"] = prim.get("style")
                row["runes_secondary_style"] = sec.get("style")
                # 4 prim + 2 sec en général
                sels = []
                for st in styles:
                    for sel in (st.get("selections") or []):
                        sels.append(sel.get("perk"))
                for j, perk in enumerate(sels[:6]):
                    row[f"rune_{j+1}"] = perk
        except Exception:
            pass
        rows.append(row)
    df = pd.DataFrame(rows)
    # ordonner colonnes: basics -> items -> spells -> runes
    item_cols = [f"item{i}" for i in range(7)]
    rune_cols = [c for c in df.columns if c.startswith("rune_")] + ["runes_primary_style","runes_secondary_style","perks_raw"]
    spell_cols = ["summoner1Id","summoner2Id"]
    ordered = [c for c in cols_basic if c in df.columns] + item_cols + spell_cols + rune_cols
    return df.reindex(columns=ordered)

def flatten_teams(info: Dict) -> pd.DataFrame:
    records = []
    for t in info.get("teams", []):
        rec = {
            "teamId": t.get("teamId"),
            "win": t.get("win"),
            "ban_0": None, "ban_1": None, "ban_2": None, "ban_3": None, "ban_4": None
        }
        # bans
        for i, b in enumerate(t.get("bans", [])[:5]):
            rec[f"ban_{i}"] = b.get("championId")
        # objectives (each has: first: bool, kills: int)
        obj = t.get("objectives", {}) or {}
        for key in ["baron","dragon","tower","inhibitor","riftHerald","champion"]:
            o = obj.get(key, {})
            rec[f"{key}_first"] = o.get("first")
            rec[f"{key}_kills"] = o.get("kills")
        records.append(rec)
    return pd.DataFrame(records)

def flatten_objectives_long(info: Dict) -> pd.DataFrame:
    # version "longue": une ligne par teamId x objectiveType
    records = []
    for t in info.get("teams", []):
        tid = t.get("teamId")
        for key, o in (t.get("objectives") or {}).items():
            records.append({
                "teamId": tid,
                "objective": key,
                "first": o.get("first"),
                "kills": o.get("kills")
            })
    return pd.DataFrame(records)

def flatten_timeline_events(timeline: Dict) -> pd.DataFrame:
    if not timeline:
        return pd.DataFrame()
    frames = timeline.get("info", {}).get("frames", [])
    recs = []
    for fr in frames:
        ts = fr.get("timestamp")
        for ev in fr.get("events", []):
            row = {"timestamp": ts, "type": ev.get("type")}
            # on stocke beaucoup de champs utiles (s'ils existent)
            for k in [
                "participantId","killerId","victimId","assistingParticipantIds","itemId",
                "skillSlot","level","levelUpType","wardType","creatorId","teamId","laneType",
                "buildingType","towerType","bounty","multiKillLength","monsterType","monsterSubType",
                "position","realTimestamp","shutdownBounty","killStreakLength","afterId","beforeId",
                "transformType","name"
            ]:
                row[k] = ev.get(k)
            recs.append(row)
    df = pd.DataFrame(recs)
    # normalise colonnes dict/list en JSON str (pour CSV)
    for c in df.columns:
        if df[c].map(lambda x: isinstance(x, (dict, list))).any():
            df[c] = df[c].map(lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, (dict, list)) else x)
    return df

# ------------------------------
# Fetchers
# ------------------------------
def get_puuid(rw: RiotWatcher, lol: LolWatcher, region: str, platform: str, game_name: str, tag_line: str) -> str:
    """Essaie d'abord account-v1 (RiotWatcher), sinon fallback summoner-v4 (LolWatcher)."""
    try:
        acct = rw.account.by_riot_id(region, game_name, tag_line)
        puuid = acct.get("puuid")
        if puuid:
            print(f"[OK] PUUID via account-v1: {puuid}")
            return puuid
    except ApiError as e:
        print(f"[WARN] account-v1 by_riot_id a échoué: {e}")

    # fallback
    summ = lol.summoner.by_name(platform, game_name)
    puuid = summ.get("puuid")
    print(f"[OK] PUUID via summoner-v4 fallback: {puuid}")
    return puuid

def pick_match_id(lol: LolWatcher, region: str, puuid: str, queue: int = 420, count: int = 50) -> str:
    """Récupère des matchIds récents, en choisit un au hasard (ou le dernier)."""
    mlist = lol.match.matchlist_by_puuid(region, puuid, type="ranked", queue=queue, count=count)
    if not mlist:
        raise SystemExit("Aucun match trouvé pour ce joueur (paramètres/queue?).")
    mid = random.choice(mlist)
    print(f"[OK] Match choisi: {mid} (parmi {len(mlist)} IDs)")
    return mid

# ------------------------------
# Main logic
# ------------------------------
def main():
    ap = argparse.ArgumentParser(description="Dump d'un seul match (JSON+CSV) via Riot API")
    ap.add_argument("--api-key", type=str, help="Clé Riot (sinon utilise RIOT_API_KEY)")
    ap.add_argument("--region", type=str, default="europe", help="Regional routing (europe/americas/asia/sea)")
    ap.add_argument("--platform", type=str, default="euw1", help="Platform routing (euw1/na1/kr/...)")
    ap.add_argument("--name", type=str, help="Riot ID - gameName (avant #)")
    ap.add_argument("--tag", type=str, help="Riot ID - tagLine (après #)")
    ap.add_argument("--queue", type=int, default=420, help="420=Ranked Solo, 440=Flex; 0 pour tout type")
    ap.add_argument("--count", type=int, default=50, help="Nb d'IDs à récupérer pour choisir un match")
    ap.add_argument("--match-id", type=str, help="Match ID complet (ex: EUW1_6999...) si tu veux cibler un match précis")
    args = ap.parse_args()

    # clé
    if args.api_key:
        os.environ["RIOT_API_KEY"] = args.api_key
    api = os.getenv("RIOT_API_KEY")
    if not api:
        raise SystemExit("RIOT_API_KEY manquante. Passe --api-key RGAPI-XXXX ou exporte la variable.")

    region = args.region.lower()
    platform = args.platform.lower()

    rw = RiotWatcher(api)   # /riot/account/v1
    lol = LolWatcher(api)   # /lol/*

    if args.match_id:
        match_id = args.match_id
        print(f"[INFO] match-id fourni: {match_id}")
    else:
        if not (args.name and args.tag):
            raise SystemExit("Sans --match-id, il faut --name et --tag (Riot ID = name#tag).")
        puuid = get_puuid(rw, lol, region, platform, args.name, args.tag)
        match_id = pick_match_id(lol, region, puuid, queue=args.queue if args.queue else None, count=args.count)

    # ---- Fetch match ----
    print(f"[STEP] Téléchargement du match: {match_id}")
    match = lol.match.by_id(region, match_id)
    save_json(DATA_DIR / f"match_{match_id}.json", match)
    info = match.get("info", {})
    print("[OK] Match JSON sauvegardé.")

    # ---- Participants CSV ----
    df_p = flatten_participants(info)
    df_to_csv(DATA_DIR / f"participants_{match_id}.csv", df_p)
    print(f"[OK] participants CSV -> {DATA_DIR / f'participants_{match_id}.csv'}")

    # ---- Teams/Objectives CSV ----
    df_t = flatten_teams(info)
    df_to_csv(DATA_DIR / f"teams_{match_id}.csv", df_t)
    df_o = flatten_objectives_long(info)
    df_to_csv(DATA_DIR / f"objectives_{match_id}.csv", df_o)
    print(f"[OK] teams/objectives CSV -> {DATA_DIR}")

    # ---- Timeline (optionnelle) ----
    print("[STEP] Téléchargement timeline (si dispo)…")
    try:
        timeline = lol.match.timeline_by_match(region, match_id)
        save_json(DATA_DIR / f"timeline_{match_id}.json", timeline)
        df_ev = flatten_timeline_events(timeline)
        if not df_ev.empty:
            df_to_csv(DATA_DIR / f"timeline_events_{match_id}.csv", df_ev)
            print(f"[OK] timeline JSON/CSV -> {DATA_DIR}")
        else:
            print("[WARN] timeline sans events parsables (ou vide).")
        # petit cooldown pour éviter 429
        time.sleep(1.0)
    except ApiError as e:
        print(f"[WARN] Timeline indisponible: {e}")

    # ---- Résumé console rapide ----
    meta = match.get("metadata", {})
    print("\n=== RÉSUMÉ ===")
    print("matchId:", meta.get("matchId"))
    print("gameVersion:", info.get("gameVersion"), "| queueId:", info.get("queueId"), "| duration(s):", info.get("gameDuration"))
    print("Équipes:", [t.get("teamId") for t in info.get("teams", [])], "| Vainqueur:", [t.get("teamId") for t in info.get("teams", []) if t.get("win")])
    print("Fichiers écrits dans:", DATA_DIR.resolve())
    print(" -", f"match_{match_id}.json")
    print(" -", f"participants_{match_id}.csv")
    print(" -", f"teams_{match_id}.csv")
    print(" -", f"objectives_{match_id}.csv")
    print(" -", f"timeline_{match_id}.json (si dispo)")
    print(" -", f"timeline_events_{match_id}.csv (si dispo)")
    print("======== FIN ========")


if __name__ == "__main__":
    main()
