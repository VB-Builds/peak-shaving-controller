# Measured results (simulated load)

Produced by `python -m peak_shaving demo`. Simulated factory load, seed 7; fixed threshold and forecast margin tuned on 2025, everything below measured on 2026.
Load 2026: 4 566 MWh, mean 521 kW, annual peak 1 653 kW, monthly peaks 1 224-1 653 kW.
Tariff: CHF 15.00/kW/month on the monthly 15-min peak; losses charged at CHF 0.1988/kWh.

## Default battery: 500 kWh / 250 kW

Fixed threshold (best on 2025): 1395 kW. Forecast margin (best on 2025): 0.00 x battery power.

| strategy | sum of monthly peaks (kW) | peak cut | annual max cut | demand saving CHF/yr | extra energy CHF/yr | net CHF/yr | cycles/yr | missed months |
|---|---|---|---|---|---|---|---|---|
| none | 17 590 | 0.0 % | 0.0 % | 0 | 0 | 0 | 0.0 | - |
| fixed | 16 391 | 6.8 % | 15.1 % | 17 981 | 18 | 17 964 | 2.0 | 1/12 |
| forecast | 15 188 | 13.7 % | 15.1 % | 36 018 | 70 | 35 948 | 7.9 | 5/12 |
| hindsight | 14 590 | 17.1 % | 15.1 % | 45 000 | 96 | 44 904 | 10.9 | - |

## Per month (kW)

| month | no battery | fixed | forecast | hindsight | forecast planned | forecast missed | reason | gap to hindsight CHF |
|---|---|---|---|---|---|---|---|---|
| 2026-01 | 1618 | 1395 | 1368 | 1368 | 1241 | yes | load above last 28 days | 0 |
| 2026-02 | 1653 | 1403 | 1403 | 1403 | 1368 | yes | load above last 28 days | 0 |
| 2026-03 | 1599 | 1395 | 1403 | 1349 | 1403 |  |  | 813 |
| 2026-04 | 1577 | 1395 | 1349 | 1327 | 1349 |  |  | 335 |
| 2026-05 | 1388 | 1388 | 1327 | 1138 | 1327 |  |  | 2831 |
| 2026-06 | 1290 | 1290 | 1138 | 1040 | 1138 |  |  | 1472 |
| 2026-07 | 1224 | 1224 | 1040 | 974 | 1040 |  |  | 983 |
| 2026-08 | 1418 | 1395 | 1168 | 1168 | 974 | yes | load above last 28 days | 0 |
| 2026-09 | 1320 | 1320 | 1168 | 1070 | 1168 |  |  | 1467 |
| 2026-10 | 1396 | 1395 | 1146 | 1146 | 1058 | yes | load above last 28 days | 0 |
| 2026-11 | 1589 | 1395 | 1339 | 1339 | 1146 | yes | load above last 28 days | 0 |
| 2026-12 | 1517 | 1395 | 1339 | 1267 | 1339 |  |  | 1082 |

Forecast vs hindsight: CHF 8982/yr in total; CHF 0 in missed months (0 with a random event at the peak: CHF 0; 5 where the load simply ran above the last 28 days: CHF 0), CHF 8982 in months it held its threshold (threshold planned higher than needed).

## Battery size sweep (power = 0.5 x capacity)

| kWh | kW | fixed net CHF/yr | forecast net CHF/yr | hindsight net CHF/yr | forecast peak cut | forecast cycles/yr | forecast missed |
|---|---|---|---|---|---|---|---|
| 100 | 50 | 273 | 4 162 | 8 995 | 1.6 % | 1.9 | 5/12 |
| 200 | 100 | 2 415 | 10 339 | 17 985 | 3.9 % | 2.8 | 5/12 |
| 300 | 150 | 6 939 | 18 577 | 26 969 | 7.0 % | 4.1 | 5/12 |
| 400 | 200 | 12 334 | 26 978 | 35 944 | 10.2 % | 5.6 | 5/12 |
| 600 | 300 | 25 792 | 44 903 | 53 845 | 17.1 % | 10.8 | 5/12 |
| 800 | 400 | 44 403 | 61 793 | 70 480 | 23.5 % | 20.3 | 6/12 |
| 1000 | 500 | 65 150 | 73 683 | 85 164 | 28.1 % | 32.6 | 6/12 |
| 1300 | 650 | 81 613 | 84 821 | 99 046 | 32.6 % | 53.1 | 6/12 |
| 1600 | 800 | 84 893 | 91 254 | 102 729 | 35.2 % | 60.1 | 6/12 |
| 2000 | 1000 | 85 085 | 95 751 | 105 721 | 37.1 % | 63.7 | 6/12 |

Extra net saving per extra kWh per year vs the assumed annualized cost CHF 37.7/kWh/yr (CHF 450/kWh installed, 15 years, 3 %):

| step (kWh) | fixed | forecast | hindsight |
|---|---|---|---|
| 0 -> 100 | 2.7 | 41.6 | 90.0 |
| 100 -> 200 | 21.4 | 61.8 | 89.9 |
| 200 -> 300 | 45.2 | 82.4 | 89.8 |
| 300 -> 400 | 53.9 | 84.0 | 89.7 |
| 400 -> 600 | 67.3 | 89.6 | 89.5 |
| 600 -> 800 | 93.1 | 84.5 | 83.2 |
| 800 -> 1000 | 103.7 | 59.5 | 73.4 |
| 1000 -> 1300 | 54.9 | 37.1 | 46.3 |
| 1300 -> 1600 | 10.9 | 21.4 | 12.3 |
| 1600 -> 2000 | 0.5 | 11.2 | 7.5 |

- fixed: extra kWh stops paying above 1300 kWh; best swept size 1300 kWh (net saving minus annualized cost CHF 32 609/yr)
- forecast: extra kWh stops paying above 1000 kWh; best swept size 1000 kWh (net saving minus annualized cost CHF 35 988/yr)
- hindsight: extra kWh stops paying above 1300 kWh; best swept size 1300 kWh (net saving minus annualized cost CHF 50 042/yr)

## Robustness: 5 other seeds, 500 kWh / 250 kW

| strategy | net CHF/yr mean | min | max | peak cut mean | missed months mean |
|---|---|---|---|---|---|
| fixed | 18 129 | 11 544 | 24 110 | 6.8 % | 2.4 |
| forecast | 37 599 | 36 331 | 38 507 | 14.2 % | 6.8 |
| hindsight | 44 912 | 44 893 | 44 923 | 17.0 % | - |

Run time: 614 s.
