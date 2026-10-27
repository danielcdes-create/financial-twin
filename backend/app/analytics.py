import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal
from app.economic_policy import EXPENSE_REASONS, amount as decimal_amount, effects, duplicate_of_ledger
from app.category_filter import display_category
from app.economic_period import economic_month
from app.financial_time import local_date


def calculate_financial_totals(transactions, confirmed_provisionals=()):
    income = Decimal('0')
    expenses = Decimal('0')
    categories = {}
    identities = {str(item['_id']) for item in transactions if item.get('_id') is not None}
    for captured, records in ((False, transactions), (True, confirmed_provisionals)):
        for item in records:
            if captured and duplicate_of_ledger(item, identities):
                continue
            result = effects(item, captured)
            income += result['income']
            expenses += result['expenses']
            if result['role'] in {'expense', 'refund'}:
                category = display_category(item.get('categoria_sugerida' if captured else 'categoria'))
                categories[category] = categories.get(category, Decimal('0')) + result['expenses']
    return {'income': income, 'expenses': expenses, 'net_flow': income - expenses, 'categories': categories}


def _pct_change(current: Decimal, previous: Decimal) -> float | None:
    if previous == 0:
        return None
    return round(float((current - previous) / abs(previous) * Decimal('100')), 1)


def _shift_ym(year: int, month: int, delta_months: int) -> tuple[int, int, str]:
    idx = year * 12 + (month - 1) + delta_months
    y, m = idx // 12, (idx % 12) + 1
    return y, m, f'{y:04d}-{m:02d}'


def _short_date_br(d: date) -> str:
    return f'{d.day:02d}/{d.month:02d}'


def build_cost_control_summary(
    transactions,
    confirmed_provisionals=(),
    *,
    anchor_date: date | None = None,
    period: str = 'month',
    period_income: Decimal = Decimal('0'),
    period_expenses: Decimal = Decimal('0'),
    previous_period_expenses: Decimal | None = None,
    recurring_total: Decimal = Decimal('0'),
    tax_total: Decimal = Decimal('0'),
    financial_cost_total: Decimal = Decimal('0'),
) -> dict:
    """Build MTD vs. prior-month same-period pacing, weekly/monthly series, and cost control insights."""
    ref_date = anchor_date or date.today()
    cur_year, cur_month = ref_date.year, ref_date.month
    cur_ym = f'{cur_year:04d}-{cur_month:02d}'
    cur_days_in_month = calendar.monthrange(cur_year, cur_month)[1]
    mtd_day = min(max(1, ref_date.day), cur_days_in_month)

    prev_year, prev_month, prev_ym = _shift_ym(cur_year, cur_month, -1)
    prev_days_in_month = calendar.monthrange(prev_year, prev_month)[1]
    prev_mtd_day = min(mtd_day, prev_days_in_month)

    # 7-month window for monthly comparison series (M-6..M0)
    _, _, oldest_ym = _shift_ym(cur_year, cur_month, -6)
    month_keys_window = [_shift_ym(cur_year, cur_month, offset)[2] for offset in range(-6, 1)]
    month_totals_map = {
        ym: {'income': Decimal('0'), 'expenses': Decimal('0')}
        for ym in month_keys_window
    }

    # 8-week window (56 days ending on ref_date) for rolling weekly comparison
    week_ranges = []
    for w_idx in range(8):
        # w_idx 0 = oldest (W-7), w_idx 7 = current week (W0)
        offset_from_current = 7 - w_idx
        w_end = ref_date - timedelta(days=offset_from_current * 7)
        w_start = w_end - timedelta(days=6)
        week_ranges.append((w_start, w_end))
    oldest_week_start = week_ranges[0][0]
    week_expenses = [Decimal('0') for _ in range(8)]
    week_incomes = [Decimal('0') for _ in range(8)]

    # Intra-month buckets for cur_ym and prev_ym: S1(1-7), S2(8-14), S3(15-21), S4(22-28), S5(29+)
    bucket_bounds = [
        ('S1', 'Sem 1', '01–07', 1, 7),
        ('S2', 'Sem 2', '08–14', 8, 14),
        ('S3', 'Sem 3', '15–21', 15, 21),
        ('S4', 'Sem 4', '22–28', 22, 28),
        ('S5', 'Sem 5', '29+', 29, 31),
    ]
    cur_bucket_mtd = [Decimal('0') for _ in range(5)]
    prev_bucket_mtd = [Decimal('0') for _ in range(5)]
    prev_bucket_full = [Decimal('0') for _ in range(5)]

    cur_mtd_expenses = Decimal('0')
    cur_full_expenses = Decimal('0')
    cur_mtd_income = Decimal('0')
    prev_mtd_expenses = Decimal('0')
    prev_full_expenses = Decimal('0')
    prev_mtd_income = Decimal('0')

    cur_mtd_categories: dict[str, Decimal] = {}
    prev_mtd_categories: dict[str, Decimal] = {}

    identities = {str(item['_id']) for item in transactions if item.get('_id') is not None}

    def _bucket_index(day_num: int) -> int:
        if day_num <= 7:
            return 0
        if day_num <= 14:
            return 1
        if day_num <= 21:
            return 2
        if day_num <= 28:
            return 3
        return 4

    for captured, records in ((False, transactions), (True, confirmed_provisionals)):
        for item in records:
            if captured and duplicate_of_ledger(item, identities):
                continue
            ev_raw = item.get('data_evento')
            ev_date = local_date(ev_raw) if isinstance(ev_raw, datetime) else None
            m_key = economic_month(item)
            if not m_key and ev_date is not None:
                m_key = ev_date.strftime('%Y-%m')

            in_month_window = bool(m_key and oldest_ym <= m_key <= cur_ym)
            in_week_window = bool(ev_date is not None and oldest_week_start <= ev_date <= ref_date)
            if not in_month_window and not in_week_window:
                continue

            eff = effects(item, captured)
            inc = eff['income']
            exp = eff['expenses']
            is_exp_role = eff['role'] in {'expense', 'refund'}
            if inc == 0 and exp == 0:
                continue

            if in_month_window and m_key in month_totals_map:
                month_totals_map[m_key]['income'] += inc
                month_totals_map[m_key]['expenses'] += exp

            if in_week_window and ev_date is not None:
                days_ago = (ref_date - ev_date).days
                w_slot = 7 - (days_ago // 7)
                if 0 <= w_slot < 8:
                    week_expenses[w_slot] += exp
                    week_incomes[w_slot] += inc

            if m_key == cur_ym:
                eff_day = (
                    ev_date.day
                    if (ev_date is not None and ev_date.strftime('%Y-%m') == cur_ym)
                    else min(ev_date.day, cur_days_in_month) if ev_date is not None else 1
                )
                cur_full_expenses += exp
                if eff_day <= mtd_day:
                    cur_mtd_expenses += exp
                    cur_mtd_income += inc
                    b_idx = _bucket_index(eff_day)
                    cur_bucket_mtd[b_idx] += exp
                    if is_exp_role and exp != 0:
                        cat = display_category(item.get('categoria_sugerida' if captured else 'categoria'))
                        cur_mtd_categories[cat] = cur_mtd_categories.get(cat, Decimal('0')) + exp

            elif m_key == prev_ym:
                eff_day = (
                    ev_date.day
                    if (ev_date is not None and ev_date.strftime('%Y-%m') == prev_ym)
                    else min(ev_date.day, prev_days_in_month) if ev_date is not None else 1
                )
                prev_full_expenses += exp
                b_idx = _bucket_index(eff_day)
                prev_bucket_full[b_idx] += exp
                if eff_day <= prev_mtd_day:
                    prev_mtd_expenses += exp
                    prev_mtd_income += inc
                    prev_bucket_mtd[b_idx] += exp
                    if is_exp_role and exp != 0:
                        cat = display_category(item.get('categoria_sugerida' if captured else 'categoria'))
                        prev_mtd_categories[cat] = prev_mtd_categories.get(cat, Decimal('0')) + exp

    # Build cumulative intra-month buckets
    mtd_buckets = []
    cur_cum = Decimal('0')
    prev_mtd_cum = Decimal('0')
    prev_full_cum = Decimal('0')
    for idx, (b_key, b_label, b_range, b_start, _b_end) in enumerate(bucket_bounds):
        reached = b_start <= mtd_day
        c_val = cur_bucket_mtd[idx]
        pm_val = prev_bucket_mtd[idx]
        pf_val = prev_bucket_full[idx]
        if reached:
            cur_cum += c_val
        prev_mtd_cum += pm_val
        prev_full_cum += pf_val
        mtd_buckets.append({
            'key': b_key,
            'label': b_label,
            'range_label': b_range,
            'current_expenses': round(float(c_val), 2),
            'previous_mtd_expenses': round(float(pm_val), 2),
            'previous_full_expenses': round(float(pf_val), 2),
            'current_cumulative': round(float(cur_cum), 2) if reached else None,
            'previous_mtd_cumulative': round(float(prev_mtd_cum), 2),
            'previous_full_cumulative': round(float(prev_full_cum), 2),
            'delta_pct': _pct_change(c_val, pm_val) if reached else None,
            'reached': reached,
        })

    mtd_delta_amount = cur_mtd_expenses - prev_mtd_expenses
    mtd_delta_pct = _pct_change(cur_mtd_expenses, prev_mtd_expenses)
    daily_burn_cur = cur_mtd_expenses / Decimal(str(max(1, mtd_day)))
    daily_burn_prev = prev_mtd_expenses / Decimal(str(max(1, prev_mtd_day)))
    projected_month_exp = max(cur_full_expenses, daily_burn_cur * Decimal(str(cur_days_in_month)))
    projected_vs_prev_full_pct = _pct_change(projected_month_exp, prev_full_expenses)

    mtd_summary = {
        'reference_date': ref_date.isoformat(),
        'current_month': cur_ym,
        'previous_month': prev_ym,
        'day_of_month': mtd_day,
        'days_in_month': cur_days_in_month,
        'previous_days_compared': prev_mtd_day,
        'current_period_label': f'01/{cur_month:02d} a {mtd_day:02d}/{cur_month:02d}',
        'previous_period_label': f'01/{prev_month:02d} a {prev_mtd_day:02d}/{prev_month:02d}',
        'current_expenses': round(float(cur_mtd_expenses), 2),
        'previous_mtd_expenses': round(float(prev_mtd_expenses), 2),
        'previous_full_expenses': round(float(prev_full_expenses), 2),
        'current_income': round(float(cur_mtd_income), 2),
        'previous_mtd_income': round(float(prev_mtd_income), 2),
        'delta_amount': round(float(mtd_delta_amount), 2),
        'delta_pct': mtd_delta_pct,
        'daily_burn_current': round(float(daily_burn_cur), 2),
        'daily_burn_previous': round(float(daily_burn_prev), 2),
        'projected_month_expenses': round(float(projected_month_exp), 2),
        'projected_vs_previous_full_pct': projected_vs_prev_full_pct,
        'buckets': mtd_buckets,
    }

    # Build rolling 4-week comparison series (W-3, W-2, W-1, W0 compared against preceding week)
    weekly_series = []
    week_labels = ['Sem -3', 'Sem -2', 'Sem -1', 'Atual']
    for idx in range(4):
        slot = 4 + idx  # slots 4, 5, 6, 7
        prev_slot = slot - 1  # immediate prior week
        w_start, w_end = week_ranges[slot]
        c_exp = week_expenses[slot]
        p_exp = week_expenses[prev_slot]
        c_inc = week_incomes[slot]
        weekly_series.append({
            'key': f'W-{3 - idx}' if idx < 3 else 'W0',
            'label': week_labels[idx],
            'period_label': f'{_short_date_br(w_start)}–{_short_date_br(w_end)}',
            'current_expenses': round(float(c_exp), 2),
            'previous_expenses': round(float(p_exp), 2),
            'current_income': round(float(c_inc), 2),
            'delta_amount': round(float(c_exp - p_exp), 2),
            'delta_pct': _pct_change(c_exp, p_exp),
        })

    rolling_4w_exp = sum(week_expenses[4:8], Decimal('0'))
    prior_4w_exp = sum(week_expenses[0:4], Decimal('0'))
    weekly_summary = {
        'current_week_expenses': round(float(week_expenses[7]), 2),
        'previous_week_expenses': round(float(week_expenses[6]), 2),
        'week_over_week_pct': _pct_change(week_expenses[7], week_expenses[6]),
        'rolling_4w_expenses': round(float(rolling_4w_exp), 2),
        'prior_4w_expenses': round(float(prior_4w_exp), 2),
        'rolling_4w_change_pct': _pct_change(rolling_4w_exp, prior_4w_exp),
        'weekly_avg_current': round(float(rolling_4w_exp / Decimal('4')), 2),
        'weekly_avg_previous': round(float(prior_4w_exp / Decimal('4')), 2),
    }

    # Build 6-month comparison series (each month paired with M-1)
    monthly_comparison_series = []
    for idx in range(1, len(month_keys_window)):
        ym = month_keys_window[idx]
        prev_m = month_keys_window[idx - 1]
        c_tot = month_totals_map[ym]
        p_tot = month_totals_map[prev_m]
        c_exp = c_tot['expenses']
        p_exp = p_tot['expenses']
        c_inc = c_tot['income']
        monthly_comparison_series.append({
            'month': ym,
            'label': f'{ym[5:7]}/{ym[2:4]}',
            'current_expenses': round(float(c_exp), 2),
            'previous_expenses': round(float(p_exp), 2),
            'income': round(float(c_inc), 2),
            'net': round(float(c_inc - c_exp), 2),
            'delta_amount': round(float(c_exp - p_exp), 2),
            'delta_pct': _pct_change(c_exp, p_exp),
        })

    # Category drivers (MTD current vs MTD previous month)
    all_cats = set(cur_mtd_categories) | set(prev_mtd_categories)
    category_drivers = []
    for cat in all_cats:
        c_val = cur_mtd_categories.get(cat, Decimal('0'))
        p_val = prev_mtd_categories.get(cat, Decimal('0'))
        if c_val <= 0 and p_val <= 0:
            continue
        diff = c_val - p_val
        category_drivers.append({
            'category': cat,
            'current_amount': round(float(c_val), 2),
            'previous_amount': round(float(p_val), 2),
            'delta_amount': round(float(diff), 2),
            'delta_pct': _pct_change(c_val, p_val),
        })
    category_drivers.sort(key=lambda x: (abs(x['delta_amount']), x['current_amount']), reverse=True)

    biggest_increase = next(
        (d for d in sorted(category_drivers, key=lambda x: x['delta_amount'], reverse=True) if d['delta_amount'] > 0),
        None,
    )
    biggest_saving = next(
        (d for d in sorted(category_drivers, key=lambda x: x['delta_amount']) if d['delta_amount'] < 0),
        None,
    )

    # Determine overall cost control status and actionable insights
    active_cur_exp = cur_mtd_expenses if period == 'month' else period_expenses
    active_prev_exp = prev_mtd_expenses if period == 'month' else (previous_period_expenses or Decimal('0'))
    active_delta = active_cur_exp - active_prev_exp
    active_pct = _pct_change(active_cur_exp, active_prev_exp)

    if active_prev_exp <= 0 and active_cur_exp <= 0:
        status = 'no_baseline'
        status_label = 'Sem gastos no período'
        headline = 'Ainda não há saídas registradas para comparar o ritmo de custos.'
    elif active_prev_exp <= 0:
        status = 'no_baseline'
        status_label = 'Base anterior em formação'
        headline = (
            f'Acumulado de R$ {cur_mtd_expenses:.2f} até {mtd_summary["current_period_label"]} '
            f'(ritmo de R$ {daily_burn_cur:.2f}/dia). Sem base no período anterior para comparação direta.'
        )
    elif active_pct is not None and active_pct <= -3.0:
        status = 'controlling'
        status_label = 'Custos sob controle'
        headline = (
            f'Você reduziu os gastos em {abs(active_pct):.1f}% (-R$ {abs(active_delta):.2f}) '
            f'em relação ao mesmo período anterior ({mtd_summary["previous_period_label"]}).'
        )
    elif active_pct is not None and active_pct <= 5.0:
        status = 'attention' if (biggest_increase and biggest_increase['delta_amount'] > 50) else 'stable'
        status_label = 'Ritmo estável · atenção' if status == 'attention' else 'Ritmo estável'
        sign_str = '+' if active_delta >= 0 else '-'
        headline = (
            f'Gastos próximos ao período anterior ({sign_str}{abs(active_pct):.1f}% · '
            f'{sign_str}R$ {abs(active_delta):.2f} vs. {mtd_summary["previous_period_label"]}).'
        )
    else:
        status = 'over_pace'
        status_label = 'Acima do ritmo anterior'
        headline = (
            f'Atenção: os gastos acumulados estão +{active_pct:.1f}% (+R$ {active_delta:.2f}) '
            f'acima do mesmo período do mês anterior ({mtd_summary["previous_period_label"]}).'
        )

    insights = []
    # Insight 1: Daily burn rate & End-of-month projection vs full previous month
    if cur_mtd_expenses > 0 or prev_full_expenses > 0:
        proj_diff = projected_month_exp - prev_full_expenses
        if prev_full_expenses > 0:
            proj_tone = 'good' if proj_diff <= 0 else ('warning' if projected_vs_prev_full_pct and projected_vs_prev_full_pct <= 8 else 'bad')
            proj_dir = 'abaixo' if proj_diff <= 0 else 'acima'
            insights.append({
                'id': 'burn_projection',
                'tone': proj_tone,
                'title': 'Ritmo diário e projeção do mês',
                'detail': (
                    f'Velocidade atual de R$ {daily_burn_cur:.2f}/dia (vs. R$ {daily_burn_prev:.2f}/dia no mês passado). '
                    f'Mantendo esse ritmo, o mês projeta fechar R$ {abs(proj_diff):.2f} {proj_dir} do fechamento anterior.'
                ),
                'metric': f'R$ {daily_burn_cur:.2f}/dia',
            })
        else:
            insights.append({
                'id': 'burn_projection',
                'tone': 'neutral',
                'title': 'Velocidade diária de gastos (MTD)',
                'detail': f'Ritmo médio de R$ {daily_burn_cur:.2f}/dia nos primeiros {mtd_day} dias do mês.',
                'metric': f'R$ {daily_burn_cur:.2f}/dia',
            })

    # Insight 2: Top category savings (Positive reinforcement)
    if biggest_saving is not None and biggest_saving['delta_amount'] < 0:
        sav_abs = abs(biggest_saving['delta_amount'])
        sav_pct = biggest_saving['delta_pct']
        pct_piece = f' ({sav_pct:.1f}%)' if sav_pct is not None else ''
        insights.append({
            'id': 'top_saving_category',
            'tone': 'good',
            'title': f'Economia em {biggest_saving["category"]}',
            'detail': (
                f'Caiu de R$ {biggest_saving["previous_amount"]:.2f} para R$ {biggest_saving["current_amount"]:.2f} '
                f'no comparativo MTD, poupando R$ {sav_abs:.2f}{pct_piece}.'
            ),
            'metric': f'-R$ {sav_abs:.2f}',
            'category': biggest_saving['category'],
        })

    # Insight 3: Top category pressure (Actionable target)
    if biggest_increase is not None and biggest_increase['delta_amount'] > 0:
        inc_abs = biggest_increase['delta_amount']
        inc_pct = biggest_increase['delta_pct']
        pct_piece = f' (+{inc_pct:.1f}%)' if inc_pct is not None else ' (sem gasto na base anterior)'
        insights.append({
            'id': 'top_pressure_category',
            'tone': 'bad' if inc_abs >= 100 or (inc_pct is not None and inc_pct >= 20) else 'warning',
            'title': f'Maior pressão: {biggest_increase["category"]}',
            'detail': (
                f'Subiu de R$ {biggest_increase["previous_amount"]:.2f} para R$ {biggest_increase["current_amount"]:.2f} '
                f'até o dia {mtd_day}, adicionando +R$ {inc_abs:.2f}{pct_piece} aos custos.'
            ),
            'metric': f'+R$ {inc_abs:.2f}',
            'category': biggest_increase['category'],
        })

    # Insight 4: Budget rigidity / Fixed & financial charges vs variable margin
    base_exp = period_expenses if period_expenses > 0 else cur_mtd_expenses
    structural = recurring_total + tax_total + financial_cost_total
    if base_exp > 0 and structural > 0:
        rigidity_pct = min(Decimal('100'), (structural / base_exp) * Decimal('100'))
        variable_amount = max(Decimal('0'), base_exp - structural)
        insights.append({
            'id': 'cost_rigidity',
            'tone': 'warning' if rigidity_pct >= 65 else 'neutral',
            'title': 'Margem de controle imediato (fixos vs. variáveis)',
            'detail': (
                f'Recorrentes, impostos e encargos somam R$ {structural:.2f} ({rigidity_pct:.0f}% das saídas). '
                f'Restam R$ {variable_amount:.2f} em gastos variáveis onde cortes têm efeito imediato.'
            ),
            'metric': f'{rigidity_pct:.0f}% fixo/encargos',
        })
    elif period_income > 0 and base_exp > 0:
        commitment_pct = (base_exp / period_income) * Decimal('100')
        savings_pct = Decimal('100') - commitment_pct
        insights.append({
            'id': 'income_commitment',
            'tone': 'good' if commitment_pct <= 80 else ('warning' if commitment_pct <= 100 else 'bad'),
            'title': 'Comprometimento das entradas no período',
            'detail': (
                f'As saídas consumiram {commitment_pct:.1f}% das entradas registradas no período '
                f'({("margem líquida de +" + f"{savings_pct:.1f}%") if savings_pct >= 0 else ("déficit de " + f"{abs(savings_pct):.1f}%")}).'
            ),
            'metric': f'{commitment_pct:.1f}% da renda',
        })

    return {
        'status': status,
        'status_label': status_label,
        'headline': headline,
        'mtd': mtd_summary,
        'weekly_series': weekly_series,
        'weekly_summary': weekly_summary,
        'monthly_comparison_series': monthly_comparison_series,
        'category_drivers': category_drivers[:6],
        'biggest_increase': biggest_increase,
        'biggest_saving': biggest_saving,
        'insights': insights,
    }
