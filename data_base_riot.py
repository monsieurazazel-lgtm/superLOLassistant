#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Collecte N matchs LoL en CSV, SANS restriction de rang.
- Seed via ladder high tiers (MASTER -> GRANDMASTER -> CHALLENGER), shard en minuscules (euw1/na1/…)
- Pas d'usage de summoner.by_name (compat anciennes versions)
- Option: fournir des seeds manuellement (--seed-ids ou --seed-puuids)

Sorties :
  - participants.csv : matchId, teamId, teamWin, winnerTeamId, role, championName,
                       kills, deaths, assists, kda_ratio, summoner1Id, summoner2Id, puuid
  - matches.csv      : matchId, winnerTeamId
"""

from __future__ import annotations
import argparse, os, time, random, collections
from pathlib import Path
from typing import Any, Dict, List, Set, Deque, Tuple
import pandas as pd

# --------- Riot deps ----------
try:
    from riotwatcher import LolWatcher, RiotWatcher, ApiError
except Exception as e:
    raise SystemExit("Installe: pip install riotwatcher pandas\n" + str(e))

# --------- Rôles ----------
ROLE_MAP = {"TOP":"top","JUNGLE":"jungle","MIDDLE":"mid","BOTTOM":"bot","UTILITY":"sup"}

# --------- Rate limit ----------
SLEEP_PER_CALL = 1.3      # ≈92 req / 2 min (clé dev ~100 / 2min)
BACKOFF_429    = 3.0

def sleep_brief(): time.sleep(SLEEP_PER_CALL)

def safe_call(fn, *args, **kwargs):
    while True:
        try:
            res = fn(*args, **kwargs)
            sleep_brief()
            return res
        except ApiError as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code == 429:
                time.sleep(BACKOFF_429); continue
            if code in (401, 403):
                raise SystemExit("Clé API invalide/expirée (401/403). Mets RIOT_API_KEY à jour.")
            raise

# --------- Extraction ----------
def extract_winner_team_id(info: Dict) -> int | None:
    wins = [t.get("teamId") for t in (info.get("teams") or []) if t.get("win")]
    return wins[0] if wins else None

def iter_participant_rows(match: Dict) -> List[Dict[str, Any]]:
    meta = match.get("metadata", {}); info = match.get("info", {})
    match_id = meta.get("matchId"); winner_team = extract_winner_team_id(info)
    out=[]
    for p in info.get("participants", []):
        role = ROLE_MAP.get((p.get("teamPosition") or "").upper())
        if not role:  # ignore ARAM/unk
            continue
        k=int(p.get("kills",0)); d=int(p.get("deaths",0)); a=int(p.get("assists",0))
        kda=(k+a)/(d if d>0 else 1)
        out.append({
            "matchId": match_id,
            "teamId": p.get("teamId"),
            "teamWin": bool(p.get("win")),
            "winnerTeamId": winner_team,
            "role": role,
            "championName": p.get("championName"),
            "kills": k, "deaths": d, "assists": a,
            "kda_ratio": float(f"{kda:.3f}"),
            "summoner1Id": p.get("summoner1Id"),
            "summoner2Id": p.get("summoner2Id"),
            "puuid": p.get("puuid"),
        })
    return out

def rows_schema() -> List[str]:
    return ["matchId","teamId","teamWin","winnerTeamId","role","championName",
            "kills","deaths","assists","kda_ratio","summoner1Id","summoner2Id","puuid"]

# --------- IO ----------
def save_append_csv(path: Path, rows: List[Dict[str, Any]], header: bool) -> None:
    if not rows: return
    df=pd.DataFrame(rows, columns=rows_schema())
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, mode=("w" if header else "a"), index=False, header=header)

def save_matches_csv(path: Path, match_rows: List[Tuple[str,int|None]], header: bool) -> None:
    if not match_rows: return
    df=pd.DataFrame(match_rows, columns=["matchId","winnerTeamId"])
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, mode=("w" if header else "a"), index=False, header=header)

# --------- Seed (sans by_name) ----------
def league_entries_pages(lol, platform_lc: str, queue_str: str, tier_en: str, div: str, max_pages: int = 10) -> List[dict]:
    """
    Compat signature: riotwatcher a varié dans l'ordre des params.
    On essaie d'abord (platform, queue, tier, division, page), puis (platform, tier, division, queue, page).
    IMPORTANT: on utilise le shard en minuscules (euw1), pas en majuscules.
    """
    all_entries=[]
    for pg in range(1, max_pages+1):
        entries=[]
        try:
            entries = safe_call(lol.league.entries, platform_lc, queue_str, tier_en, div, page=pg)
        except ApiError:
            entries=[]
        if not entries:
            try:
                entries = safe_call(lol.league.entries, platform_lc, tier_en, div, queue_str, page=pg)
            except ApiError:
                entries=[]
        if not entries:
            # page vide -> on continue (les pages sup peuvent être vides, selon l’implémentation)
            continue
        all_entries.extend(entries)
    return all_entries

def seed_from_ladder_hightiers(lol: LolWatcher, platform_lc: str, queue_str: str) -> List[str]:
    """
    Récupère des summonerId via high tiers (MASTER -> GM -> CHALL).
    Utilise EXCLUSIVEMENT le shard en minuscules (euw1).
    """
    ids: List[str] = []

    # MASTER
    try:
        data = safe_call(lol.league.masters_by_queue, platform_lc, queue_str)
        ids += [e.get("summonerId") for e in (data.get("entries") or []) if e.get("summonerId")]
    except ApiError:
        pass

    # GRANDMASTER
    if not ids:
        try:
            data = safe_call(lol.league.grandmaster_by_queue, platform_lc, queue_str)
            ids += [e.get("summonerId") for e in (data.get("entries") or []) if e.get("summonerId")]
        except ApiError:
            pass

    # CHALLENGER
    if not ids:
        try:
            data = safe_call(lol.league.challenger_by_queue, platform_lc, queue_str)
            ids += [e.get("summonerId") for e in (data.get("entries") or []) if e.get("summonerId")]
        except ApiError:
            pass

    # Elargissement DIAMOND si toujours rien (certaines configs renvoient 0)
    if not ids:
        for div in ["I","II","III","IV"]:
            entries = league_entries_pages(lol, platform_lc, queue_str, "DIAMOND", div, max_pages=10)
            ids += [e.get("summonerId") for e in entries if e.get("summonerId")]
            if ids: break

    # dédup
    return list({x for x in ids if x})

def summoner_ids_to_puuids(lol: LolWatcher, platform_lc: str, summ_ids: List[str]) -> List[str]:
    puuids=[]
    for sid in summ_ids:
        try:
            s = safe_call(lol.summoner.by_id, platform_lc, sid)   # <-- by_id existe chez toi
            if s.get("puuid"): puuids.append(s["puuid"])
        except ApiError:
            pass
    return list({p for p in puuids if p})

# --------- Collecte ----------
def collect_dataset(
    api_key: str,
    region: str,            # europe/americas/asia/sea (match-v5)
    platform: str,          # euw1/na1/kr/... (league/summoner)
    target_matches: int,
    queue_id: int | None,
    outdir: Path,
    matchlist_count: int = 100,
    max_seed_players: int = 300,
    seed_ids: List[str] | None = None,     # summonerId seeds (optionnel)
    seed_puuids: List[str] | None = None,  # puuid seeds (optionnel)
):
    rw = RiotWatcher(api_key)
    lol = LolWatcher(api_key)

    # Compat éventuelle (certaines vieilles versions)
    if not hasattr(lol.league, "masters_by_queue") and hasattr(lol.league, "master_by_queue"):
        lol.league.masters_by_queue = lol.league.master_by_queue

    platform_lc = platform.lower().strip()   # CRUCIAL: rester en minuscules (euw1)
    QUEUE_STR = "RANKED_SOLO_5x5" if (queue_id == 420 or queue_id is None) else "RANKED_FLEX_SR"

    outdir.mkdir(parents=True, exist_ok=True)
    part_csv = outdir / "participants.csv"
    match_csv = outdir / "matches.csv"
    save_append_csv(part_csv, [], header=True)
    save_matches_csv(match_csv, [], header=True)

    # 1) Seeds
    seeds_puuids: List[str] = []

    # a) PUUIDs fournis ?
    if seed_puuids:
        seeds_puuids = list({p for p in seed_puuids if p})

    # b) summonerIds fournis ?
    elif seed_ids:
        # on convertit ces IDs en PUUIDs
        seeds_puuids = summoner_ids_to_puuids(lol, platform_lc, list({x for x in seed_ids if x}))

    # c) sinon, ladder high tiers (MASTER -> GM -> CHALL -> DIAMOND pages)
    else:
        summ_ids = seed_from_ladder_hightiers(lol, platform_lc, QUEUE_STR)
        if not summ_ids:
            raise SystemExit("Impossible de récupérer des seeds via le ladder (essaie --seed-ids ou --seed-puuids).")
        if max_seed_players and len(summ_ids) > max_seed_players:
            random.shuffle(summ_ids); summ_ids = summ_ids[:max_seed_players]
        seeds_puuids = summoner_ids_to_puuids(lol, platform_lc, summ_ids)

    if not seeds_puuids:
        raise SystemExit("Aucun PUUID seed disponible (essaie --seed-ids ou --seed-puuids).")

    # 2) Parcours (snowball)
    puuid_queue: Deque[str] = collections.deque(seeds_puuids)
    seen_puuids: Set[str] = set(seeds_puuids)
    seen_matches: Set[str] = set()

    processed = 0
    batch_rows: List[Dict[str, Any]] = []
    batch_match_rows: List[Tuple[str, int | None]] = []

    print(f"[RUN] cible={target_matches} matchs, queue_id={queue_id}, seeds={len(seeds_puuids)}")

    while processed < target_matches and puuid_queue:
        puuid = puuid_queue.popleft()

        # matchlist par puuid (sans filtre de rang, seulement queue si fournie)
        kw={}
        if queue_id:
            kw["queue"]=queue_id
            kw["type"]="ranked"
        try:
            mlist = safe_call(lol.match.matchlist_by_puuid, region, puuid, count=matchlist_count, **kw)
        except ApiError:
            continue
        if not mlist: continue

        for mid in mlist:
            if processed >= target_matches: break
            if mid in seen_matches: continue
            try:
                match = safe_call(lol.match.by_id, region, mid)
            except ApiError:
                continue
            info = match.get("info", {})
            if not info or not info.get("participants"): continue

            p_rows = iter_participant_rows(match)
            if not p_rows: continue

            winner_team = extract_winner_team_id(info)
            batch_rows.extend(p_rows)
            batch_match_rows.append((mid, winner_team))
            seen_matches.add(mid)
            processed += 1

            # snowball: on ajoute tous les puuids vus
            for pr in p_rows:
                pu = pr["puuid"]
                if pu and pu not in seen_puuids:
                    seen_puuids.add(pu)
                    puuid_queue.append(pu)

            # flush périodique
            if len(batch_rows) >= 500:
                save_append_csv(part_csv, batch_rows, header=False)
                save_matches_csv(match_csv, batch_match_rows, header=False)
                print(f"[SAVE] {processed}/{target_matches} matchs")
                batch_rows.clear(); batch_match_rows.clear()

    # flush final
    if batch_rows:
        save_append_csv(part_csv, batch_rows, header=False)
        save_matches_csv(match_csv, batch_match_rows, header=False)
        print(f"[SAVE] Flush final : +{len(batch_match_rows)} matchs")

    print(f"[DONE] Matchs collectés: {processed}.")
    print(f"participants.csv -> {part_csv.resolve()}")
    print(f"matches.csv      -> {match_csv.resolve()}")

# --------- CLI ----------
def main():
    ap = argparse.ArgumentParser(description="Collecte N matchs (sans restriction de rang)")
    ap.add_argument("--api-key", type=str, help="Clé Riot (sinon utilise RIOT_API_KEY)")
    ap.add_argument("--region", type=str, default="europe", help="Routing match-v5 (europe/americas/asia/sea)")
    ap.add_argument("--platform", type=str, default="euw1", help="Shard league/summoner (euw1/na1/kr/...)")
    ap.add_argument("--target", type=int, default=1000, help="Nombre de matchs à collecter")
    ap.add_argument("--queue", type=int, default=420, help="420=SoloQ, 440=Flex, 0=toutes files")
    ap.add_argument("--matchlist-count", type=int, default=100, help="Nb d'IDs par puuid (max 100)")
    ap.add_argument("--outdir", type=str, default="data_db", help="Dossier de sortie")
    ap.add_argument("--max-seed-players", type=int, default=300, help="Limite de seeds initiaux")

    # Seeds manuels (optionnels)
    ap.add_argument("--seed-ids", type=str, help="summonerId seeds, séparés par des virgules")
    ap.add_argument("--seed-ids-file", type=str, help="fichier texte avec un summonerId par ligne")
    ap.add_argument("--seed-puuids", type=str, help="PUUID seeds, séparés par des virgules")
    ap.add_argument("--seed-puuids-file", type=str, help="fichier texte avec un PUUID par ligne")

    args = ap.parse_args()

    if args.api_key: os.environ["RIOT_API_KEY"]=args.api_key
    api_key = os.getenv("RIOT_API_KEY")
    if not api_key:
        raise SystemExit("RIOT_API_KEY absente. Fournis --api-key RGAPI-XXXX ou exporte la variable.")

    region   = args.region.lower().strip()
    platform = args.platform.lower().strip()

    seed_ids: List[str] = []
    if args.seed_ids:
        seed_ids += [s.strip() for s in args.seed_ids.split(",") if s.strip()]
    if args.seed_ids_file and Path(args.seed_ids_file).exists():
        with open(args.seed_ids_file, "r", encoding="utf-8") as f:
            seed_ids += [ln.strip() for ln in f if ln.strip()]

    seed_puuids: List[str] = []
    if args.seed_puuids:
        seed_puuids += [s.strip() for s in args.seed_puuids.split(",") if s.strip()]
    if args.seed_puuids_file and Path(args.seed_puuids_file).exists():
        with open(args.seed_puuids_file, "r", encoding="utf-8") as f:
            seed_puuids += [ln.strip() for ln in f if ln.strip()]

    collect_dataset(
        api_key=api_key,
        region=region,
        platform=platform,
        target_matches=args.target,
        queue_id=(args.queue if args.queue != 0 else None),
        outdir=Path(args.outdir),
        matchlist_count=max(1, min(100, args.matchlist_count)),
        max_seed_players=max(50, args.max_seed_players),
        seed_ids=(seed_ids or None),
        seed_puuids=(seed_puuids or None),
    )

if __name__ == "__main__":
    main()
