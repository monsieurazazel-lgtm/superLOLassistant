#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
lol_matchups_test.py (version corrigée + verbeuse)

USAGE EXEMPLES
--------------
# 1) Démo offline (sans clé)
python lol_matchups_test.py --demo --build

# 2) Collecte Riot API + build (clé passée en argument)
python lol_matchups_test.py --riot --api-key RGAPI-XXXX \
  --platform EUW1 --region europe --name ztheo17 --tag EUW --count 100 --build

# 3) Recommandations (après build)
python lol_matchups_test.py --recommend --role mid --enemy Zed --topk 5 --min-games 20
"""

from __future__ import annotations
import argparse
import json
import os
import random
import time
from pathlib import Path
import pandas as pd

# ===============================
#          CONSTANTES
# ===============================
DATA_DIR = Path("data")
RAW_PATH = DATA_DIR / "matches_raw.jsonl"
MATCHUPS_CSV = DATA_DIR / "matchups.csv"

ROLE_MAP = {
    "TOP": "top",
    "JUNGLE": "jungle",
    "MIDDLE": "mid",
    "BOTTOM": "bot",
    "UTILITY": "sup",
}

DEMO_CHAMPS = ["Ahri","Zed","Yone","Orianna","Annie","Garen","Darius","Jax","Camille","Riven",
               "LeeSin","Vi","Sejuani","Kayn","Graves","Jinx","Caitlyn","Ashe","Xayah","Ezreal",
               "Thresh","Lulu","Leona","Nautilus","Morgana"]
DEMO_ROLES = ["top","jungle","mid","bot","sup"]


# ===============================
#           DEMO MODE
# ===============================
def demo_generate_matches(n_matches: int = 200) -> pd.DataFrame:
    """
    Génère des matchs synthétiques (5 rôles x 2 équipes) pour tester la chaîne complète.
    """
    rows = []
    for m in range(n_matches):
        match_id = f"DEMO_{m:06d}"
        ally = {r: random.choice(DEMO_CHAMPS) for r in DEMO_ROLES}
        enemy = {r: random.choice([c for c in DEMO_CHAMPS if c != ally[r]] or DEMO_CHAMPS) for r in DEMO_ROLES}

        bias = 0.0
        if ally["bot"] == "Jinx" and ally["sup"] == "Thresh": bias += 0.02
        if enemy["bot"] == "Jinx" and enemy["sup"] == "Thresh": bias -= 0.02
        ally_win = random.random() < (0.50 + bias)

        for r in DEMO_ROLES:
            rows.append({"matchId": match_id, "teamId": 100, "win": ally_win, "role": r, "champ": ally[r]})
            rows.append({"matchId": match_id, "teamId": 200, "win": (not ally_win), "role": r, "champ": enemy[r]})
    return pd.DataFrame(rows)


def save_raw_from_df(df: pd.DataFrame) -> None:
    """
    Écrit un JSONL brut au format "proche Riot" pour réutiliser le même parseur.
    """
    DATA_DIR.mkdir(exist_ok=True)
    inv = {v: k for k, v in ROLE_MAP.items()}
    with RAW_PATH.open("w", encoding="utf-8") as f:
        for mid, sub in df.groupby("matchId"):
            parts = []
            for _, row in sub.iterrows():
                parts.append({
                    "teamId": int(row["teamId"]),
                    "win": bool(row["win"]),
                    "teamPosition": inv.get(row["role"], "MIDDLE"),
                    "championName": row["champ"],
                })
            m = {"metadata": {"matchId": mid}, "info": {"participants": parts, "gameVersion": "DEMO-1.0"}}
            f.write(json.dumps(m) + "\n")


# ===============================
#         RIOT API MODE
# ===============================
def riot_collect(api_key: str, platform: str, region: str,
                 game_name: str, tag_line: str,
                 queue: int = 420, count: int = 200, pause_sec: float = 1.2) -> None:
    """
    1) Récupère PUUID via account-v1 (RiotWatcher), avec fallback via summoner-v4 si besoin
    2) Récupère une liste de matchIds (match-v5)
    3) Télécharge les matchs (match-v5.by_id) et append dans data/matches_raw.jsonl
    """
    print("[RIOT] Import des clients Riot…")
    try:
        # RiotWatcher: pour /riot/account/v1
        # LolWatcher : pour /lol/... (match, summoner, league, etc.)
        from riotwatcher import RiotWatcher, LolWatcher, ApiError
    except Exception as e:
        raise SystemExit("riotwatcher n'est pas installé. Fais: pip install riotwatcher\n" + str(e))

    rw = RiotWatcher(api_key)  # account-v1
    lol = LolWatcher(api_key)  # lol/match-v5 + summoner-v4

    DATA_DIR.mkdir(exist_ok=True)

    # 1) PUUID
    print(f"[RIOT] account.by_riot_id(region={region}, name={game_name}, tag={tag_line})")
    try:
        acct = rw.account.by_riot_id(region, game_name, tag_line)
        puuid = acct.get("puuid")
        if not puuid:
            raise ValueError("PUUID manquant dans la réponse account-v1")
        print("[RIOT] PUUID acquis via account-v1")
    except Exception as e:
        print(f"[RIOT] Impossible via account-v1 ({e}). Fallback summoner-v4 avec platform={platform} …")
        # Fallback : ancien flux par nom de summoner (sans tag)
        try:
            summ = lol.summoner.by_name(platform, game_name)
            puuid = summ.get("puuid")
            if not puuid:
                raise ValueError("PUUID manquant dans la réponse summoner-v4")
            print("[RIOT] PUUID acquis via summoner-v4 (fallback)")
        except ApiError as ee:
            raise SystemExit(f"[RIOT] summoner.by_name ERROR: {ee}")

    # 2) Liste de matchs
    print(f"[RIOT] matchlist_by_puuid(region={region}, count={count}, queue={queue})")
    try:
        match_ids = lol.match.matchlist_by_puuid(region, puuid, type="ranked", queue=queue, count=count)
    except ApiError as e:
        raise SystemExit(f"[RIOT] matchlist_by_puuid ERROR: {e}")
    print(f"[RIOT] {len(match_ids)} matchIds récupérés")

    # Dé-duplication
    seen = set()
    if RAW_PATH.exists():
        with RAW_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    m = json.loads(line)
                    mid0 = m.get("metadata", {}).get("matchId")
                    if mid0:
                        seen.add(mid0)
                except Exception:
                    pass

    # 3) Téléchargement des matchs
    print("[RIOT] Téléchargement des matchs…")
    fetched = 0
    with RAW_PATH.open("a", encoding="utf-8") as f:
        for i, mid in enumerate(match_ids, 1):
            if mid in seen:
                continue
            try:
                mat = lol.match.by_id(region, mid)
                f.write(json.dumps(mat) + "\n")
                fetched += 1
                if i % 10 == 0:
                    print(f"[RIOT] {i}/{len(match_ids)} traités ({fetched} nouveaux)")
                time.sleep(pause_sec)  # spacing simple pour éviter 429
            except ApiError as e:
                if getattr(e, "response", None) and e.response.status_code == 429:
                    print("[RIOT] 429 rate limit → pause 3s")
                    time.sleep(3.0)
                else:
                    print(f"[RIOT] Skip {mid}: {e}")
    print(f"[RIOT] Terminé. Nouveaux matchs: {fetched}. Fichier: {RAW_PATH}")


# ===============================
#     PARSING & MATCHUPS
# ===============================
def flatten_matches(jsonl_path: Path) -> pd.DataFrame:
    """
    Transforme le JSONL brut en DF (matchId, teamId, win, role, champ), 
    garde seulement les matchs avec 5 rôles par équipe (10 lignes).
    """
    rows = []
    if not jsonl_path.exists():
        return pd.DataFrame(columns=["matchId","teamId","win","role","champ"])

    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                m = json.loads(line)
            except Exception:
                continue
            info = m.get("info", {})
            parts = info.get("participants", [])
            if not parts:
                continue
            for p in parts:
                role_key = (p.get("teamPosition") or "").upper()
                role = ROLE_MAP.get(role_key)
                if not role:
                    # ignore ARAM / positions inconnues
                    continue
                rows.append({
                    "matchId": m.get("metadata", {}).get("matchId"),
                    "teamId": p.get("teamId"),
                    "win": bool(p.get("win")),
                    "role": role,
                    "champ": p.get("championName"),
                })

    df = pd.DataFrame(rows)
    valid = df.groupby("matchId").size().eq(10)  # 5 rôles x 2 équipes
    df = df[df["matchId"].isin(valid[valid].index)]
    print(f"[BUILD] Matches valides (5 rôles x 2 équipes): {df['matchId'].nunique()}")
    return df


def compute_lane_matchups(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule les winrates A vs B par rôle (duels lane-vs-lane).
    """
    if df.empty:
        return pd.DataFrame(columns=["role","champ_ally","champ_enemy","games","wins","winrate"])

    left  = df[df.teamId == 100].groupby(["matchId","role"]).first().reset_index()
    right = df[df.teamId == 200].groupby(["matchId","role"]).first().reset_index()
    duel = left.merge(right, on=["matchId","role"], suffixes=("_ally","_enemy"))

    duel["ally_win"] = duel["win_ally"].astype(int)
    grp = duel.groupby(["role","champ_ally","champ_enemy"]).agg(
        games=("ally_win","size"),
        wins=("ally_win","sum"),
    ).reset_index()

    grp["winrate"] = grp["wins"] / grp["games"].where(grp["games"].ne(0), 1)
    grp = grp.sort_values(["role","champ_ally","games"], ascending=[True,True,False])
    print(f"[BUILD] Paires rôle-vs-rôle: {len(grp)}")
    return grp


def save_matchups_csv(df: pd.DataFrame) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    df.to_csv(MATCHUPS_CSV, index=False)
    print(f"[BUILD] matchups.csv écrit dans {MATCHUPS_CSV}")


def recommend(role: str, enemy: str, topk: int = 5, min_games: int = 20) -> pd.DataFrame:
    if not MATCHUPS_CSV.exists():
        raise SystemExit("matchups.csv introuvable. Lance d'abord --build (démo ou Riot).")
    m = pd.read_csv(MATCHUPS_CSV)
    sub = m[(m["role"]==role) & (m["champ_enemy"]==enemy) & (m["games"]>=min_games)]
    sub = sub.sort_values("winrate", ascending=False).head(topk)
    return sub[["role","champ_ally","champ_enemy","games","wins","winrate"]]


# ===============================
#               CLI
# ===============================
def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Matchups LoL (A vs B par rôle) - Test rapide")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="Génère des matchs synthétiques (offline).")
    mode.add_argument("--riot", action="store_true", help="Collecte via Riot API (vrais matchs).")
    mode.add_argument("--recommend", action="store_true", help="Recommande les meilleurs picks vs un champion.")

    # Riot / routing
    p.add_argument("--api-key", type=str, help="Clé Riot (alternative à la variable d'environnement RIOT_API_KEY)")
    p.add_argument("--platform", type=str, default="EUW1", help="Plateforme (EUW1/NA1/KR/BR1/...)")
    p.add_argument("--region", type=str, default="europe", help="Regional routing pour match-v5 (europe/americas/asia/sea)")
    p.add_argument("--name", type=str, default=None, help="gameName (Riot ID avant le #)")
    p.add_argument("--tag", type=str, default=None, help="tagLine (Riot ID après le #)")
    p.add_argument("--queue", type=int, default=420, help="420=Ranked Solo, 440=Flex")
    p.add_argument("--count", type=int, default=200, help="Nb de matchs à collecter")

    # Build & Recommend
    p.add_argument("--build", action="store_true", help="Construit matchups.csv depuis data/matches_raw.jsonl")
    p.add_argument("--role", type=str, default="mid", help="Rôle (top/jungle/mid/bot/sup)")
    p.add_argument("--enemy", type=str, default="Zed", help="Champion ennemi ciblé")
    p.add_argument("--topk", type=int, default=5, help="Top K recommandations")
    p.add_argument("--min-games", type=int, default=20, help="Seuil minimal de parties")
    return p


def main():
    args = build_argparser().parse_args()

    # Gestion de la clé
    if args.api_key:
        os.environ["RIOT_API_KEY"] = args.api_key
    api_key = os.getenv("RIOT_API_KEY")

    if args.demo:
        df_demo = demo_generate_matches(n_matches=200)
        save_raw_from_df(df_demo)
        print(f"[DEMO] Données brutes écrites dans {RAW_PATH}")
        if args.build:
            df = flatten_matches(RAW_PATH)
            matchups = compute_lane_matchups(df)
            save_matchups_csv(matchups)
            print(matchups.head(10).to_string(index=False))
        return

    if args.riot:
        if not api_key:
            raise SystemExit("RIOT_API_KEY absente. Fournis --api-key RGAPI-XXXX ou exporte la variable.")
        if not args.name or not args.tag:
            raise SystemExit("--name et --tag requis (Riot ID = gameName#tagLine).")
        riot_collect(api_key=api_key, platform=args.platform, region=args.region,
                     game_name=args.name, tag_line=args.tag,
                     queue=args.queue, count=args.count)
        if args.build:
            df = flatten_matches(RAW_PATH)
            matchups = compute_lane_matchups(df)
            save_matchups_csv(matchups)
            print(matchups.head(10).to_string(index=False))
        return

    if args.recommend:
        rec = recommend(role=args.role, enemy=args.enemy, topk=args.topk, min_games=args.min_games)
        if rec.empty:
            print("Aucune reco (pas assez de données ou mauvais rôle/ennemi).")
        else:
            print(rec.to_string(index=False))
        return


if __name__ == "__main__":
    main()
