# Backtest for Cont & Kukanov allocator

This repository provides a Python script (`backtest.py`) that implements and back‑tests the Cont & Kukanov static allocator for limit‑order execution. It also compares its performance against three common benchmarks: Best‑Ask, TWAP, and VWAP.

---

## Table of Contents

1. [Overview](#overview)
2. [Requirements](#requirements)
3. [Installation](#installation)
4. [Usage](#usage)
5. [Script Structure](#script-structure)

   * [Loading Data](#loading-data)
   * [Cost & Allocation](#cost--allocation)
   * [Back‑Test Loop](#back-test-loop)
   * [Baselines](#baselines)
   * [Grid Search](#grid-search)
   * [Output](#output)
6. [Example](#example)
7. [Configuring Parameters](#configuring-parameters)

---

## Overview

`backtest.py` reads a CSV of time‑stamped Level‑1 order‑book messages, runs a static allocator to split a fixed‑size buy order across venues, simulates execution over time, and measures total cost and fill. It then runs three baseline strategies (Best‑Ask, TWAP, VWAP) on the same data, and reports cost, fill, average price, and basis‑point savings.

---

## Requirements

* Python 3.7+
* [NumPy](https://numpy.org/)
* [Pandas](https://pandas.pydata.org/)

Install dependencies with:

```bash
pip install numpy pandas
```

---

## Installation

1. Clone this repository:

   ```bash
   git clone https://github.com/TejusR/cont-kukanov-allocator.git
   cd cont-kukanov-allocator
   ```

2. Ensure your L1 feed is in CSV format with columns:
   `ts_event,publisher_id,ask_px_00,ask_sz_00`

---

## Usage

```bash
python backtest.py --data path/to/l1_day.csv
```

* \`\`: Path to the CSV file (defaults to `l1_day.csv` in the working directory).

The script will print a JSON summary to STDOUT.

---

## Script Structure

### Loading Data

```python
def load_snapshots(path):
    df = pd.read_csv(path)
    df = df.sort_values('ts_event')
    return df
```

* Reads the CSV feed.
* Sorts by timestamp (`ts_event`) so snapshots are processed in chronological order.

### Cost & Allocation

* \`\`
  Calculates total expected cost:

  * Execution cost = price × shares + fee
  * Maker rebates for unexecuted limits
  * Penalties for underfill (`lambda_under`) and overfill (`lambda_over`)
  * Queue‑risk cost (`theta_queue` × mis‑execution)

* \`\`
  Brute‑forces all splits of `order_size` (in `step`‑share increments) across venues, picks the split with minimal cost.

### Back‑Test Loop

```python
for _, group in snapshots.groupby('ts_event'):
    # build venue list, call allocate(), simulate fills, update cost & remaining
```

* Iterates snapshot‑by‑snapshot.
* Uses the static allocator on the **remaining** shares.
* Executes each slice up to displayed depth.
* Continues until order is filled or data ends.

### Baselines

* **Best‑Ask**: Always hit the lowest ask price across venues each snapshot.
* **TWAP**: Evenly splits order across time buckets (per‑minute), hits cost within each bucket.
* **VWAP**: Static split proportional to first‑snapshot displayed depth, then executes that split over time.

### Grid Search

A small user‑configurable grid over `(λ_over, λ_under, θ_queue)` is evaluated. The script picks the parameter triple that yields the lowest total cost.

```python
λ_over_vals  = [0.0, 0.1, 0.2]
λ_under_vals = [0.0, 0.1, 0.2]
θ_queue_vals = [0.0, 0.05, 0.1]
```

### Output

Prints a JSON object containing:

* `best_params`: optimal penalties
* `router`, `best_ask`, `twap`, `vwap`: each with `cost`, `fill`, `avg_price`
* `savings_bps`: basis‑point savings of the allocator vs. each baseline

---

## Example

```python
{'best_params': {'lambda_over': 0.1, 'lambda_under': 0.0, 'theta_queue': 0.05},
 'router':    {'cost':  502350.0, 'fill': 5000, 'avg_price': 100.47},
 'best_ask':  {'cost':  503125.0, 'fill': 5000, 'avg_price': 100.625},
 'twap':      {'cost':  502900.0, 'fill': 5000, 'avg_price': 100.58},
 'vwap':      {'cost':  502600.0, 'fill': 5000, 'avg_price': 100.52},
 'savings_bps': {
     'vs_best_ask': 15.5,
     'vs_twap':     11.0,
     'vs_vwap':      5.0
 }}
```

---

## Configuring Parameters

* **Order size**: Modify `ORDER_SIZE` in `main()`.
* **Grid ranges**: Tweak the penalty lists for finer optimization.
* **Step size**: Change the `step` argument in `allocate()` to control granularity vs. runtime.
* **Fees & rebates**: Populate the `fee` and `rebate` fields in `run_router()` from your exchange schedule.

---

