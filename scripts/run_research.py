"""Run the v2 research end to end and save results for the website.

  1. ML dataset from the SPY archive          -> data/cache/ml_dataset.parquet
  2. Walk-forward evaluation of the models     (train on past years, test on the next)
  3. Backtest of buying 0DTE options           -> data/cache/trades.parquet
  4. Model vs market on the same moments       (who forecast the move better?)
  5. Does the model make money? straddles + single options grouped by signal
  6. Final model trained on everything         -> models/move_model.joblib
  7. Live insights: similar trades, realistic-chance curves, hold-vs-sell, exit rules -> models/insights.json
  8. Summary for the web page                  -> web/research.json

Run:  .venv/bin/python scripts/run_research.py [--rebuild]
"""
import json
import sys
from datetime import datetime, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import joblib
import numpy as np
import pandas as pd

from checker.history_check import verdict
from core.black_scholes import prob_itm
from core.expiry import ET, MINUTES_PER_YEAR
from data import archive
from research import backtest as bt
from research import exits as ex
from research import insights as ins
from data.events import event_type
from research import move_model as mm
from research import strategies as sl
from research import trade_model as tm

ROOT = Path(__file__).resolve().parents[1]
MODEL_FILE = ROOT / "models" / "move_model.joblib"
TRADE_MODEL_FILE = ROOT / "models" / "trade_model.joblib"
RESULTS_FILE = ROOT / "web" / "research.json"
INSIGHTS_FILE = ROOT / "models" / "insights.json"
REBUILD = "--rebuild" in sys.argv
# The production ML move model. gbm + VIX1D beat plain gbm on the market-matched test (QLIKE 0.287 vs 0.330),
# so it is the signal used by the trade score, the strategy gate and the live app.
ML_SIGNAL = "gbm_vix1d"
ML_EXTRA = ("vix1d_prev",)


def cached(path: Path, build):
    if path.exists() and not REBUILD:
        return pd.read_parquet(path)
    df = build()
    df.to_parquet(path)
    return df


def records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records", date_format="iso"))


def main():
    spy, vix = archive.load_spy(), archive.load_vix()
    print(f"SPY archive: {spy.index[0]:%Y-%m-%d} → {spy.index[-1]:%Y-%m-%d}")

    # 1-2. ML dataset + walk-forward
    data = cached(archive.CACHE / "ml_dataset.parquet", lambda: mm.build_dataset(spy, vix))
    data["date"] = pd.to_datetime(data["date"]).dt.date
    print(f"ML dataset: {len(data):,} rows, {data['date'].nunique():,} days")
    data["vix1d_prev"] = [v / 100 for v in archive.previous_close(archive.load_vix1d(), data["date"])]
    wf = mm.walk_forward(data, variants={"gbm_vix1d": ("gbm", ("vix1d_prev",))})
    by_year = []
    for y, g in wf.groupby([d.year for d in wf["date"]]):
        for k in ("seasonal", "har", "gbm"):
            by_year.append({"year": int(y), "model": k, **mm.score(g["rv_rest"].to_numpy(), g["ret_rest"].to_numpy(), g[f"pred_{k}"].to_numpy())})
    by_year = pd.DataFrame(by_year)
    overall = pd.DataFrame([{"model": k, **mm.score(wf["rv_rest"].to_numpy(), wf["ret_rest"].to_numpy(), wf[f"pred_{k}"].to_numpy())}
                            for k in ("seasonal", "har", "gbm")])
    print("\nWalk-forward (all test years):\n", overall.to_string(index=False))

    # 3. Backtest
    days = archive.option_days()
    trades = cached(archive.CACHE / "trades.parquet",
                    lambda: bt.run(spy, archive.load_options, days, progress=True))
    trades["date"] = pd.to_datetime(trades["date"]).dt.date
    print(f"\nBacktest: {len(trades):,} trades over {trades['date'].nunique()} days")

    # 4. Model vs market at the same moments (top of each hour, 2024+)
    atm = (trades.assign(dist=(trades["strike"] - trades["spot"]).abs())
           .sort_values("dist").groupby(["date", "time"]).head(2)          # ATM call + put
           .groupby(["date", "time"]).agg(atm_iv=("iv", "mean"), minutes_left=("minutes_left", "first")).reset_index())
    atm["implied_rv"] = atm["atm_iv"] ** 2 * atm["minutes_left"] / MINUTES_PER_YEAR
    both = wf.merge(atm[["date", "time", "implied_rv", "atm_iv"]], on=["date", "time"])
    vs = [{"model": "market (ATM implied vol)",
           **mm.score(both["rv_rest"].to_numpy(), both["ret_rest"].to_numpy(), both["implied_rv"].to_numpy())}]
    vs += [{"model": k, **mm.score(both["rv_rest"].to_numpy(), both["ret_rest"].to_numpy(), both[f"pred_{k}"].to_numpy())}
           for k in ("seasonal", "har", "gbm", "gbm_vix1d")]
    # simple blend: geometric mean of market and model
    blend = np.sqrt(both["implied_rv"].to_numpy() * both["pred_gbm"].to_numpy())
    vs.append({"model": "blend (market × gbm)", **mm.score(both["rv_rest"].to_numpy(), both["ret_rest"].to_numpy(), blend)})
    vs = pd.DataFrame(vs)
    print(f"\nModel vs market on {len(both):,} matching moments:\n", vs.to_string(index=False))

    # 5a. Straddles grouped by model/market ratio
    straddle = (trades.assign(dist=(trades["strike"] - trades["spot"]).abs())
                .sort_values("dist").groupby(["date", "time", "type"]).head(1)
                .groupby(["date", "time"]).filter(lambda g: len(g) == 2 and g["strike"].nunique() == 1)
                .groupby(["date", "time"]).agg(entry=("entry", "sum"), payoff=("payoff", "sum")).reset_index())
    straddle = straddle.merge(both[["date", "time", f"pred_{ML_SIGNAL}", "implied_rv"]], on=["date", "time"])
    straddle["ratio"] = np.sqrt(straddle[f"pred_{ML_SIGNAL}"] / straddle["implied_rv"])     # model vol / market vol
    straddle["pnl"] = straddle["payoff"] - straddle["entry"]
    straddle["itm"] = straddle["payoff"] > 0
    straddle["prob_itm"] = np.nan
    straddle["signal"] = pd.qcut(straddle["ratio"], 3, labels=["model says smaller move", "about equal", "model says bigger move"])
    straddle_summary = bt.summarize(straddle, "signal")
    ratio_bounds = straddle.groupby("signal", observed=True)["ratio"].agg(["min", "max"]).reset_index()
    print("\nATM straddles by model/market ratio:\n", straddle_summary.to_string(index=False))

    # 5b. Single options: history verdict (v1) and model verdict (v2)
    hourly = wf[wf["time"].str.endswith(":00")][["date", "time", f"pred_{ML_SIGNAL}", "minutes_left"]].rename(columns={f"pred_{ML_SIGNAL}": "pred_gbm"})
    t2 = trades.merge(hourly.rename(columns={"minutes_left": "ml"}), on=["date", "time"], how="left")
    sig = np.sqrt(t2["pred_gbm"] / (t2["ml"] / MINUTES_PER_YEAR))
    t2["model_prob"] = [prob_itm(S, K, m / MINUTES_PER_YEAR, bt.RATE, s, cp) if s == s else np.nan
                        for S, K, m, s, cp in zip(t2["spot"], t2["strike"], t2["minutes_left"], sig, t2["type"])]
    t2["model_verdict"] = [verdict(pm, p) if p == p else None for pm, p in zip(t2["prob_itm"], t2["model_prob"])]
    hist_summary = bt.summarize(t2.dropna(subset=["verdict"]), "verdict")
    model_summary = bt.summarize(t2.dropna(subset=["model_verdict"]), "model_verdict")
    all_summary = bt.summarize(t2.assign(all="all trades"), "all")
    otm = t2[t2["moneyness"] > 0].assign(bucket=lambda d: pd.cut(d["moneyness"], [0, 1, 2, 3, 5], labels=["$0–1 OTM", "$1–2 OTM", "$2–3 OTM", "$3–5 OTM"]))
    otm_summary = bt.summarize(otm, "bucket")
    time_summary = bt.summarize(t2, "time")
    print("\nAll trades:\n", all_summary.to_string(index=False))
    print("\nBy history verdict (v1):\n", hist_summary.to_string(index=False))
    print("\nBy model verdict (v2):\n", model_summary.to_string(index=False))

    # cost sensitivity on the headline number
    sens = []
    for mult in (0, 1, 2):
        e = t2["price"] + mult * t2["price"].map(bt.half_spread)
        sens.append({"spread_multiplier": mult, "return_per_dollar": float((t2["payoff"] - e).sum() / e.sum())})

    calib = {
        "market": records(bt.calibration(t2, "prob_itm")),
        "history": records(bt.calibration(t2, "hist_prob")),
        "model": records(bt.calibration(t2, "model_prob")),
    }

    # 5c. Insights for the live checker
    t3 = ins.add_buckets(t2, atm.set_index(["date", "time"])["atm_iv"])
    t3["prob_profit"] = [prob_itm(S, K + e if cp == "call" else K - e, m / MINUTES_PER_YEAR, bt.RATE, iv, cp)
                         for S, K, e, m, iv, cp in zip(t3["spot"], t3["strike"], t3["entry"], t3["minutes_left"], t3["iv"], t3["type"])]
    t3["profit"] = t3["pnl"] > 0
    similar = records(pd.DataFrame(ins.similar_table(t3, bt.summarize)))
    curves = {"itm": ins.fit_curve(t3["prob_itm"].to_numpy(), t3["itm"].to_numpy()),
              "profit": ins.fit_curve(t3["prob_profit"].to_numpy(), t3["profit"].to_numpy())}
    curve_eval = {"itm": ins.evaluate_curve(t3, "prob_itm", "itm"),
                  "profit": ins.evaluate_curve(t3, "prob_profit", "profit")}
    print("\nRealistic-chance correction, scored on later unseen days (Brier, lower is better):")
    for k, v in curve_eval.items():
        print(f"  {k:6s} market {v['brier_market']:.4f} → realistic {v['brier_realistic']:.4f}  ({v['n_test']:,} trades from {v['test_from']})")

    # 5d. After you buy: hold vs sell now, and exit rules
    states = cached(archive.CACHE / "states.parquet", lambda: ex.build_states(spy, archive.load_options, days, progress=True))
    states["date"] = pd.to_datetime(states["date"]).dt.date
    hold_table = records(pd.DataFrame(ex.hold_vs_sell_table(states, bt.summarize)))
    hold_all = bt.summarize(states.assign(entry=states["sell"], pnl=states["payoff"] - states["sell"],
                                          itm=states["payoff"] > 0, prob_itm=np.nan, all="all"), "all")
    print(f"\nHold vs sell: {len(states):,} position-moments; holding to the close vs selling: "
          f"{hold_all['return_per_dollar'].iloc[0]:+.1%} per $1")
    exits = cached(archive.CACHE / "exits.parquet", lambda: ex.simulate_exits(t3, archive.load_options, progress=True))
    exits["date"] = pd.to_datetime(exits["date"]).dt.date
    exits["group"] = np.select([exits["z"] < 0, exits["z"] < 0.5], ["in the money", "near the money (0–0.5 moves out)"],
                               "far out of the money (0.5+ moves)")
    exit_summary = bt.summarize(exits, "rule")
    exit_summary["label"] = exit_summary["rule"].map(ex.EXIT_RULES)
    exit_by_group = bt.summarize(exits, ["group", "rule"])
    exit_by_group["label"] = exit_by_group["rule"].map(ex.EXIT_RULES)
    print("\nExit rules (all trades):\n", exit_summary[["label", "trades", "win_rate", "return_per_dollar", "ci_low", "ci_high"]].to_string(index=False))

    # 5e. Trade score: ML that rates a specific purchase (walk-forward by month)
    tsc = tm.add_context(t3, wf.assign(pred_gbm=wf[f"pred_{ML_SIGNAL}"]), atm)
    tsc["pred"] = cached(archive.CACHE / "trade_preds.parquet",
                         lambda: tm.walk_forward(tsc).rename("pred").to_frame())["pred"].to_numpy()
    oos = tm.rate_oos(tsc.dropna(subset=["pred"]))
    y = oos["ret"].clip(*tm.CLIP)
    rating_summary = bt.summarize(oos, "rating")
    rating_summary["label"] = rating_summary["rating"].map({k: lbl for k, lbl, *_ in tm.RATINGS})
    # honest comparison: does the ML beat the simple similar-trades table, and does it rank WITHIN a group?
    cells = ["type", "z_bin", "time_bucket"]
    within = oos.groupby(cells)["pred"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 3, labels=["lower third", "middle", "upper third"]) if len(s) >= 30 else np.nan)
    within_summary = bt.summarize(oos.assign(within=within).dropna(subset=["within"]), "within")
    mlr = oos.groupby(cells)["ml_ratio"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 3, labels=["ML: smaller move", "about equal", "ML: bigger move"]) if len(s) >= 30 else np.nan)
    mlr_summary = bt.summarize(oos.assign(mlr=mlr).dropna(subset=["mlr"]), "mlr")
    spearman = float(oos["pred"].corr(y, method="spearman"))
    print(f"\nTrade score (out of sample, {len(oos):,} trades): Spearman {spearman:.3f}")
    print(rating_summary[["label", "trades", "win_rate", "return_per_dollar", "ci_low", "ci_high"]].to_string(index=False))
    final_trade = tm.new_model().fit(tm.make_features(tsc), tm.target(tsc))
    in_sample = np.sort(final_trade.predict(tm.make_features(tsc)))
    joblib.dump({"model": final_trade, "features": tm.FEATURES, "sorted_preds": in_sample[:: max(1, len(in_sample) // 2000)],
                 "ratings": records(rating_summary), "trained_through": str(max(days)), "n_trades": len(tsc),
                 "oos_spearman": spearman}, TRADE_MODEL_FILE)
    print(f"Saved {TRADE_MODEL_FILE.relative_to(ROOT)}")

    # 5f. Strategy Lab
    strat = cached(archive.CACHE / "strategies.parquet", lambda: sl.run(spy, archive.load_options, days, progress=True))
    strat["date"] = pd.to_datetime(strat["date"]).dt.date
    ctx = wf.assign(pred_gbm=wf[f"pred_{ML_SIGNAL}"])[["date", "time", "pred_gbm", "vix_prev"]].merge(atm[["date", "time", "implied_rv"]], on=["date", "time"])
    ctx["ml_ratio"] = np.sqrt(ctx["pred_gbm"] / ctx["implied_rv"])
    strat = strat.merge(ctx[["date", "time", "ml_ratio", "vix_prev"]], on=["date", "time"], how="left")
    strat["event"] = [event_type(d) or "no event" for d in strat["date"]]
    strat["ml_gate"] = pd.cut(strat["ml_ratio"], [0, 0.9, 1.1, 10], labels=["ML: smaller move (sell)", "ML ≈ market", "ML: bigger move"])
    vix_prev_all = archive.previous_close(archive.load_vix(), strat["date"])
    strat["vix_regime"] = ["VIX > 19" if v > 19 else "VIX ≤ 19" for v in vix_prev_all]
    S = lambda df, by: records(sl.summary(df, by, bt.summarize))
    lab = {}
    for name in ("short_straddle", "short_condor"):
        g = strat[strat["strategy"] == name]
        lab[name] = {"all": S(g.assign(all="all entries"), "all"),
                     "by_ml_gate": S(g.dropna(subset=["ml_gate"]), "ml_gate"),
                     "by_time": S(g, "time"), "by_event": S(g, "event"),
                     "costs": [{"mult": m, "return": float((sl.with_costs(g, m)["pnl"].sum()) / g["basis"].sum())} for m in (0, 1, 2)]}
    be = strat[strat["strategy"] == "breakeven_condor"]
    lab["breakeven_condor"] = {"by_variant_time": S(be, ["variant", "time"]),
                               "by_vix": S(be[be["time"] == "09:45"], ["variant", "vix_regime"]),
                               "stop_rate": float(be.loc[be["variant"] == "with breakeven stops", "stops"].gt(0).mean())}
    mom_opt = strat[strat["strategy"] == "momentum"]
    lab["momentum"] = {"underlying": sl.momentum_stats(sl.momentum_table(spy)),
                       "options": S(mom_opt.assign(all="every day"), "all"),
                       "options_strong": S(mom_opt[mom_opt["strong"] == True].assign(all="strong 10:00 move"), "all")}
    for name, block in lab.items():
        for key, val in block.items():
            if isinstance(val, list):
                for r in val:
                    if "return_per_dollar" in r:
                        r["verdict"] = sl.verdict(r)
    print(f"\nStrategy Lab: {len(strat):,} strategy trades")
    for name in ("short_straddle", "short_condor"):
        print(f"  {name}:", [(r["ml_gate"], round(r["return_per_dollar"], 4), round(r["gross_return"], 4), r["verdict"]) for r in lab[name]["by_ml_gate"]])
    print("  breakeven condor:", [(r["variant"], r["time"], round(r["return_per_dollar"], 4), r["verdict"]) for r in lab["breakeven_condor"]["by_variant_time"]])
    print("  momentum (underlying):", {k: (v["days"], round(v["avg_bp"], 2), round(v["ci_low_bp"], 2), round(v["ci_high_bp"], 2)) for k, v in lab["momentum"]["underlying"].items()})
    print("  momentum (options):", [(r["all"], round(r["return_per_dollar"], 4), r["verdict"]) for r in lab["momentum"]["options"] + lab["momentum"]["options_strong"]])

    # 6. Final model on all data
    final = mm.MoveModel("gbm", ML_EXTRA).fit(data)
    MODEL_FILE.parent.mkdir(exist_ok=True)
    joblib.dump({"model": final, "features": mm.FEATURES + list(ML_EXTRA), "trained_through": str(data["date"].max()),
                 "n_rows": len(data)}, MODEL_FILE)
    print(f"\nSaved {MODEL_FILE.relative_to(ROOT)} (trained through {data['date'].max()})")

    # 7. Insights the app reads at request time
    INSIGHTS_FILE.write_text(json.dumps({
        "generated": datetime.now(ET).isoformat(timespec="minutes"),
        "options_range": [str(min(days)), str(max(days))],
        "z_edges": [None if np.isinf(e) else e for e in ins.Z_EDGES],
        "similar": similar, "curves": curves, "curve_eval": curve_eval,
        "model_verdicts": records(model_summary), "all_trades": records(all_summary),
        "hold_vs_sell": hold_table, "hold_all": records(hold_all), "strategy_lab": lab,
        "exit_rules": records(exit_summary), "exit_rules_by_group": records(exit_by_group),
    }, indent=1))
    print(f"Saved {INSIGHTS_FILE.relative_to(ROOT)}")

    # 8. Results for the web page
    results = {
        "generated": datetime.now(ET).isoformat(timespec="minutes"),
        "spy_range": [str(spy.index[0].date()), str(spy.index[-1].date())],
        "options_range": [str(min(days)), str(max(days))],
        "ml": {"rows": len(data), "days": int(data["date"].nunique()),
               "overall": records(overall), "by_year": records(by_year), "features": mm.FEATURES},
        "model_vs_market": {"moments": len(both), "scores": records(vs)},
        "backtest": {
            "trades": len(t2), "days": int(t2["date"].nunique()),
            "all": records(all_summary), "by_history_verdict": records(hist_summary),
            "by_model_verdict": records(model_summary), "by_otm": records(otm_summary),
            "by_time": records(time_summary), "cost_sensitivity": sens,
        },
        "straddles": {"by_signal": records(straddle_summary), "ratio_bounds": records(ratio_bounds), "n": len(straddle)},
        "calibration": calib,
        "realistic_chance": curve_eval,
        "trade_score": {"oos_trades": len(oos), "spearman": spearman, "ratings": records(rating_summary),
                        "within_group": records(within_summary), "ml_ratio_within_group": records(mlr_summary),
                        "features": tm.FEATURES},
        "strategy_lab": lab,
        "exits": {"rules": records(exit_summary), "by_group": records(exit_by_group),
                  "hold_vs_sell_all": records(hold_all), "states": len(states)},
    }
    RESULTS_FILE.write_text(json.dumps(results, indent=1))
    print(f"Saved {RESULTS_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
