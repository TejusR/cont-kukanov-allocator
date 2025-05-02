import argparse
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

def compute_cost(split, venues, order_size, lambda_over, lambda_under, theta_queue):
    executed = 0
    cash_spent = 0.0
    for qty, v in zip(split, venues):
        exe = min(qty, v.ask_size)
        executed += exe
        cash_spent += exe * (v.ask + v.fee)
        maker_rebate = max(qty - exe, 0) * v.rebate
        cash_spent -= maker_rebate

    underfill = max(order_size - executed, 0)
    overfill  = max(executed - order_size, 0)
    risk_pen  = theta_queue * (underfill + overfill)
    cost_pen  = lambda_under * underfill + lambda_over * overfill

    return cash_spent + risk_pen + cost_pen

def allocate(order_size, venues, lambda_over, lambda_under, theta_queue, step=100):
    splits = [[]]
    for i in range(len(venues)):
        new_splits = []
        for alloc in splits:
            used = sum(alloc)
            max_v = min(order_size - used, venues[i].ask_size)
            for q in range(0, max_v+1, step):
                new_splits.append(alloc + [q])
        splits = new_splits

    best_cost = float('inf')
    best_split = []
    for alloc in splits:
        if sum(alloc) != order_size:
            continue
        cost = compute_cost(alloc, venues, order_size, lambda_over, lambda_under, theta_queue)
        if cost < best_cost:
            best_cost  = cost
            best_split = alloc
    return best_split, best_cost

def load_snapshots(path):
    df = pd.read_csv(path, parse_dates=['ts_event'])
    df = df.sort_values('ts_event')
    return df

def run_router(snapshots, order_size, lambda_over, lambda_under, theta_queue):
    remaining = order_size
    total_cost = 0.0
    history = []  # (timestamp, cumulative_cost)
    for _, raw_group in snapshots.groupby('ts_event'):
        ts = raw_group['ts_event'].iloc[0]
        history.append((ts, total_cost))

        if remaining <= 0:
            break

        group = raw_group.groupby('publisher_id', as_index=False).last()
        venues = [
            SimpleNamespace(
                ask      = row['ask_px_00'],
                ask_size = int(row['ask_sz_00']),
                fee      = 0.0,
                rebate   = 0.0
            )
            for _, row in group.iterrows()
        ]

        split, _ = allocate(remaining, venues, lambda_over, lambda_under, theta_queue)
        executed = 0
        for qty, v in zip(split, venues):
            exe = min(qty, v.ask_size)
            executed += exe
            total_cost += exe * (v.ask + v.fee)
            total_cost -= max(qty - exe, 0) * v.rebate

        remaining -= executed

    return history, total_cost, order_size - remaining

def run_best_ask(snapshots, order_size):
    remaining = order_size
    total_cost = 0.0
    history = []
    for _, raw_group in snapshots.groupby('ts_event'):
        ts = raw_group['ts_event'].iloc[0]
        history.append((ts, total_cost))

        if remaining <= 0:
            break

        group = raw_group.groupby('publisher_id', as_index=False).last()
        row = group.loc[group['ask_px_00'].idxmin()]
        exe = min(remaining, int(row['ask_sz_00']))
        total_cost += exe * row['ask_px_00']
        remaining -= exe

    return history, total_cost, order_size - remaining

def run_twap(snapshots, order_size):
    df = snapshots.copy()
    df['minute'] = df['ts_event'].dt.floor('min')
    minutes = sorted(df['minute'].unique())
    n_buckets = len(minutes)
    per_bucket = order_size // n_buckets
    extra = order_size - per_bucket * n_buckets

    remaining = order_size
    total_cost = 0.0
    history = []
    for minute in minutes:
        # record cost at start of bucket
        ts = minute
        history.append((ts, total_cost))

        target = per_bucket + (1 if extra > 0 else 0)
        extra -= 1 if extra > 0 else 0

        bucket_df = df[df['minute'] == minute]
        bucket_rem = target
        for _, row in bucket_df.groupby('publisher_id', as_index=False).last().iterrows():
            if bucket_rem <= 0:
                break
            exe = min(bucket_rem, int(row['ask_sz_00']))
            total_cost += exe * row['ask_px_00']
            bucket_rem -= exe

        remaining -= (target - bucket_rem)

    return history, total_cost, order_size - remaining

def run_vwap(snapshots, order_size):
    # first = snapshots.groupby('ts_event').first().iloc[0]
    # first_group = snapshots[snapshots['ts_event'] == first.name]
    first_ts = snapshots['ts_event'].iloc[0]
    first_group = (
        snapshots[snapshots['ts_event'] == first_ts]
          .groupby('publisher_id', as_index=False)
          .last()
    )
    depths = first_group.set_index('publisher_id')['ask_sz_00'].astype(int)
    weights = depths / depths.sum()
    split_map = (weights * order_size).astype(int).to_dict()

    remaining = dict(split_map)
    total_cost = 0.0
    history = []

    for _, raw_group in snapshots.groupby('ts_event'):
        ts = raw_group['ts_event'].iloc[0]
        history.append((ts, total_cost))

        group = raw_group.groupby('publisher_id', as_index=False).last()
        for pid, rem in list(remaining.items()):
            if rem <= 0:
                continue
            row = group[group['publisher_id'] == pid]
            if row.empty:
                continue
            row = row.iloc[0]
            exe = min(rem, int(row['ask_sz_00']))
            total_cost += exe * row['ask_px_00']
            remaining[pid] -= exe

        if sum(remaining.values()) <= 0:
            break

    return history, total_cost, order_size - sum(remaining.values())

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', default='l1_day.csv',
                        help='Path to L1 CSV feed')
    args = parser.parse_args()

    ORDER_SIZE = 5000
    snaps = load_snapshots(args.data)

    # grid search
    lambda_over_vals  = [0.0, 0.1, 0.2]
    lambda_under_vals = [0.0, 0.1, 0.2]
    theta_queue_vals = [0.0, 0.05, 0.1]

    best = {'params': None, 'cost': float('inf'), 'fill': 0}
    best_history = None
    for lo in lambda_over_vals:
        for lu in lambda_under_vals:
            for tq in theta_queue_vals:
                hist, cost, fill = run_router(snaps, ORDER_SIZE, lo, lu, tq)
                if cost < best['cost']:
                    best = {
                        'params': {'lambda_over': lo,
                                   'lambda_under': lu,
                                   'theta_queue': tq},
                        'cost': cost,
                        'fill': fill
                    }
                    best_history = hist

    # baselines
    ba_hist, ba_cost, ba_fill = run_best_ask(snaps, ORDER_SIZE)
    tw_hist, tw_cost, tw_fill = run_twap(snaps, ORDER_SIZE)
    vw_hist, vw_cost, vw_fill = run_vwap(snaps, ORDER_SIZE)

    # print JSON summary
    out = {
        'best_params': best['params'],
        'router': {
            'cost':      best['cost'],
            'fill':      best['fill'],
            'avg_price': best['cost'] / best['fill'] if best['fill']>0 else None
        },
        'best_ask': {
            'cost':      ba_cost,
            'fill':      ba_fill,
            'avg_price': ba_cost / ba_fill if ba_fill>0 else None
        },
        'twap': {
            'cost':      tw_cost,
            'fill':      tw_fill,
            'avg_price': tw_cost / tw_fill if tw_fill>0 else None
        },
        'vwap': {
            'cost':      vw_cost,
            'fill':      vw_fill,
            'avg_price': vw_cost / vw_fill if vw_fill>0 else None
        },
        'savings_bps': {
            'vs_best_ask': (ba_cost - best['cost']) / ORDER_SIZE * 1e4,
            'vs_twap':     (tw_cost - best['cost']) / ORDER_SIZE * 1e4,
            'vs_vwap':     (vw_cost - best['cost']) / ORDER_SIZE * 1e4,
        }
    }
    print(json.dumps(out, indent=2))

    # build DataFrames for plotting
    df_router = pd.DataFrame(best_history, columns=['ts_event','cum_cost'])
    df_router['ts_event'] = pd.to_datetime(df_router['ts_event'])
    df_router.set_index('ts_event', inplace=True)

    df_ba = pd.DataFrame(ba_hist, columns=['ts_event','cum_cost'])
    df_ba['ts_event'] = pd.to_datetime(df_ba['ts_event'])
    df_ba.set_index('ts_event', inplace=True)

    df_twap = pd.DataFrame(tw_hist, columns=['ts_event','cum_cost'])
    df_twap['ts_event'] = pd.to_datetime(df_twap['ts_event'])
    df_twap.set_index('ts_event', inplace=True)

    df_vwap = pd.DataFrame(vw_hist, columns=['ts_event','cum_cost'])
    df_vwap['ts_event'] = pd.to_datetime(df_vwap['ts_event'])
    df_vwap.set_index('ts_event', inplace=True)

    # plot
    plt.figure()
    plt.plot(df_router.index, df_router['cum_cost'], label='Router')
    plt.plot(df_ba.index,    df_ba['cum_cost'],    label='Best‑Ask')
    plt.plot(df_twap.index,  df_twap['cum_cost'],  label='TWAP')
    plt.plot(df_vwap.index,  df_vwap['cum_cost'],  label='VWAP')
    plt.xlabel('Time')
    plt.ylabel('Cumulative Cost')
    plt.title('Cumulative Execution Cost Over Time')
    plt.legend()
    plt.tight_layout()
    plt.show()

if __name__ == '__main__':
    main()
