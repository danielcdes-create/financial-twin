export type CostControlStatus =
  | "controlling"
  | "stable"
  | "attention"
  | "over_pace"
  | "no_baseline";

export type CostControlInsight = {
  id: string;
  tone: "good" | "warning" | "bad" | "neutral";
  title: string;
  detail: string;
  metric: string;
  category?: string;
};

export type MtdBucket = {
  key: string;
  label: string;
  range_label: string;
  current_expenses: number;
  previous_mtd_expenses: number;
  previous_full_expenses: number;
  current_cumulative: number | null;
  previous_mtd_cumulative: number;
  previous_full_cumulative: number;
  delta_pct: number | null;
  reached: boolean;
};

export type WeeklyComparisonItem = {
  key: string;
  label: string;
  period_label: string;
  current_expenses: number;
  previous_expenses: number;
  current_income: number;
  delta_amount: number;
  delta_pct: number | null;
};

export type MonthlyComparisonItem = {
  month: string;
  label: string;
  current_expenses: number;
  previous_expenses: number;
  income: number;
  net: number;
  delta_amount: number;
  delta_pct: number | null;
};

export type CategoryDriver = {
  category: string;
  current_amount: number;
  previous_amount: number;
  delta_amount: number;
  delta_pct: number | null;
};

export type CostControlViewModel = {
  status: CostControlStatus;
  status_label: string;
  headline: string;
  mtd: {
    reference_date: string;
    current_month: string;
    previous_month: string;
    day_of_month: number;
    days_in_month: number;
    previous_days_compared: number;
    current_period_label: string;
    previous_period_label: string;
    current_expenses: number;
    previous_mtd_expenses: number;
    previous_full_expenses: number;
    current_income: number;
    previous_mtd_income: number;
    delta_amount: number;
    delta_pct: number | null;
    daily_burn_current: number;
    daily_burn_previous: number;
    projected_month_expenses: number;
    projected_vs_previous_full_pct: number | null;
    buckets: MtdBucket[];
  };
  weekly_series: WeeklyComparisonItem[];
  weekly_summary: {
    current_week_expenses: number;
    previous_week_expenses: number;
    week_over_week_pct: number | null;
    rolling_4w_expenses: number;
    prior_4w_expenses: number;
    rolling_4w_change_pct: number | null;
    weekly_avg_current: number;
    weekly_avg_previous: number;
  };
  monthly_comparison_series: MonthlyComparisonItem[];
  category_drivers: CategoryDriver[];
  insights: CostControlInsight[];
};

export function formatSignedPct(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(Number(value))) return "sem base";
  const num = Number(value);
  return `${num >= 0 ? "+" : ""}${num.toFixed(1)}%`;
}

export function statusPalette(status: CostControlStatus): {
  bg: string;
  border: string;
  text: string;
  dot: string;
} {
  if (status === "controlling") {
    return {
      bg: "#0F2E29",
      border: "#2E7D6B",
      text: "#4FD1B5",
      dot: "#4FD1B5",
    };
  }
  if (status === "over_pace") {
    return {
      bg: "#311B1D",
      border: "#7A373B",
      text: "#FF8C83",
      dot: "#FF8C83",
    };
  }
  if (status === "attention" || status === "stable") {
    return {
      bg: "#2B2515",
      border: "#705D23",
      text: "#F4B942",
      dot: "#F4B942",
    };
  }
  return {
    bg: "#162629",
    border: "#315054",
    text: "#AFC1BF",
    dot: "#92A4A2",
  };
}

function pctChange(current: number, previous: number): number | null {
  if (!Number.isFinite(previous) || previous === 0) return null;
  return Math.round(((current - previous) / Math.abs(previous)) * 1000) / 10;
}

export function resolveCostControlView(
  dashboard: any,
  monthDashboard?: any,
  yearDashboard?: any,
  moneyFormatter: (v: number) => string = (v) => `R$ ${(v || 0).toFixed(2)}`,
): CostControlViewModel {
  const source = dashboard?.cost_control || monthDashboard?.cost_control;
  if (source && source.mtd && Array.isArray(source.mtd.buckets)) {
    return {
      status: (source.status as CostControlStatus) || "no_baseline",
      status_label: String(source.status_label || "Análise de ritmo"),
      headline: String(source.headline || ""),
      mtd: {
        reference_date: String(source.mtd.reference_date || ""),
        current_month: String(source.mtd.current_month || ""),
        previous_month: String(source.mtd.previous_month || ""),
        day_of_month: Number(source.mtd.day_of_month || 1),
        days_in_month: Number(source.mtd.days_in_month || 30),
        previous_days_compared: Number(source.mtd.previous_days_compared || 1),
        current_period_label: String(source.mtd.current_period_label || "Mês atual"),
        previous_period_label: String(source.mtd.previous_period_label || "Mês anterior"),
        current_expenses: Number(source.mtd.current_expenses || 0),
        previous_mtd_expenses: Number(source.mtd.previous_mtd_expenses || 0),
        previous_full_expenses: Number(source.mtd.previous_full_expenses || 0),
        current_income: Number(source.mtd.current_income || 0),
        previous_mtd_income: Number(source.mtd.previous_mtd_income || 0),
        delta_amount: Number(source.mtd.delta_amount || 0),
        delta_pct: source.mtd.delta_pct == null ? null : Number(source.mtd.delta_pct),
        daily_burn_current: Number(source.mtd.daily_burn_current || 0),
        daily_burn_previous: Number(source.mtd.daily_burn_previous || 0),
        projected_month_expenses: Number(source.mtd.projected_month_expenses || 0),
        projected_vs_previous_full_pct:
          source.mtd.projected_vs_previous_full_pct == null
            ? null
            : Number(source.mtd.projected_vs_previous_full_pct),
        buckets: (source.mtd.buckets || []).map((b: any) => ({
          key: String(b.key || ""),
          label: String(b.label || ""),
          range_label: String(b.range_label || ""),
          current_expenses: Number(b.current_expenses || 0),
          previous_mtd_expenses: Number(b.previous_mtd_expenses || 0),
          previous_full_expenses: Number(b.previous_full_expenses || 0),
          current_cumulative:
            b.current_cumulative == null ? null : Number(b.current_cumulative),
          previous_mtd_cumulative: Number(b.previous_mtd_cumulative || 0),
          previous_full_cumulative: Number(b.previous_full_cumulative || 0),
          delta_pct: b.delta_pct == null ? null : Number(b.delta_pct),
          reached: Boolean(b.reached),
        })),
      },
      weekly_series: Array.isArray(source.weekly_series)
        ? source.weekly_series.map((w: any) => ({
            key: String(w.key || ""),
            label: String(w.label || ""),
            period_label: String(w.period_label || ""),
            current_expenses: Number(w.current_expenses || 0),
            previous_expenses: Number(w.previous_expenses || 0),
            current_income: Number(w.current_income || 0),
            delta_amount: Number(w.delta_amount || 0),
            delta_pct: w.delta_pct == null ? null : Number(w.delta_pct),
          }))
        : [],
      weekly_summary: {
        current_week_expenses: Number(source.weekly_summary?.current_week_expenses || 0),
        previous_week_expenses: Number(source.weekly_summary?.previous_week_expenses || 0),
        week_over_week_pct:
          source.weekly_summary?.week_over_week_pct == null
            ? null
            : Number(source.weekly_summary.week_over_week_pct),
        rolling_4w_expenses: Number(source.weekly_summary?.rolling_4w_expenses || 0),
        prior_4w_expenses: Number(source.weekly_summary?.prior_4w_expenses || 0),
        rolling_4w_change_pct:
          source.weekly_summary?.rolling_4w_change_pct == null
            ? null
            : Number(source.weekly_summary.rolling_4w_change_pct),
        weekly_avg_current: Number(source.weekly_summary?.weekly_avg_current || 0),
        weekly_avg_previous: Number(source.weekly_summary?.weekly_avg_previous || 0),
      },
      monthly_comparison_series: Array.isArray(source.monthly_comparison_series)
        ? source.monthly_comparison_series.map((m: any) => ({
            month: String(m.month || ""),
            label: String(m.label || ""),
            current_expenses: Number(m.current_expenses || 0),
            previous_expenses: Number(m.previous_expenses || 0),
            income: Number(m.income || 0),
            net: Number(m.net || 0),
            delta_amount: Number(m.delta_amount || 0),
            delta_pct: m.delta_pct == null ? null : Number(m.delta_pct),
          }))
        : [],
      category_drivers: Array.isArray(source.category_drivers)
        ? source.category_drivers.map((c: any) => ({
            category: String(c.category || ""),
            current_amount: Number(c.current_amount || 0),
            previous_amount: Number(c.previous_amount || 0),
            delta_amount: Number(c.delta_amount || 0),
            delta_pct: c.delta_pct == null ? null : Number(c.delta_pct),
          }))
        : [],
      insights: Array.isArray(source.insights)
        ? source.insights.map((ins: any) => ({
            id: String(ins.id || ""),
            tone: (ins.tone as CostControlInsight["tone"]) || "neutral",
            title: String(ins.title || ""),
            detail: String(ins.detail || ""),
            metric: String(ins.metric || ""),
            ...(ins.category ? { category: String(ins.category) } : {}),
          }))
        : [],
    };
  }

  // Fallback synthesis when backend cost_control field is not present
  const activeDash = monthDashboard || dashboard || {};
  const curExp = Number(activeDash.expenses ?? dashboard?.expenses ?? 0);
  const curInc = Number(activeDash.income ?? dashboard?.income ?? 0);
  const comp = activeDash.comparison || dashboard?.comparison || null;
  const prevExp = Number(comp?.expenses ?? 0);
  const prevInc = Number(comp?.income ?? 0);
  const periodEndStr = String(activeDash.period_end || dashboard?.period_end || "2026-10-26");
  const dayOfMonth = Math.max(1, Number(periodEndStr.slice(8, 10)) || 26);
  const curMonthNum = Number(periodEndStr.slice(5, 7)) || 10;
  const curYearNum = Number(periodEndStr.slice(0, 4)) || 2026;
  const daysInMonth = new Date(Date.UTC(curYearNum, curMonthNum, 0)).getUTCDate() || 30;
  const prevMonthNum = curMonthNum === 1 ? 12 : curMonthNum - 1;

  const deltaAmount = curExp - prevExp;
  const deltaPct = pctChange(curExp, prevExp);
  const dailyCur = curExp / Math.max(1, dayOfMonth);
  const dailyPrev = prevExp / Math.max(1, dayOfMonth);
  const projectedExp = Math.max(curExp, dailyCur * daysInMonth);
  const projectedVsPrevPct = pctChange(projectedExp, prevExp);

  const weights = [0.22, 0.24, 0.23, 0.21, 0.1];
  const bucketDefs = [
    { key: "S1", label: "Sem 1", range: "01–07", start: 1 },
    { key: "S2", label: "Sem 2", range: "08–14", start: 8 },
    { key: "S3", label: "Sem 3", range: "15–21", start: 15 },
    { key: "S4", label: "Sem 4", range: "22–28", start: 22 },
    { key: "S5", label: "Sem 5", range: "29+", start: 29 },
  ];
  const reachedCount = Math.max(1, bucketDefs.filter((b) => b.start <= dayOfMonth).length);
  let curCum = 0;
  let prevCum = 0;
  const buckets: MtdBucket[] = bucketDefs.map((b, i) => {
    const reached = b.start <= dayOfMonth;
    const cVal = reached ? Math.round((curExp / reachedCount) * 100) / 100 : 0;
    const pVal = Math.round(prevExp * weights[i] * 100) / 100;
    if (reached) curCum += cVal;
    prevCum += pVal;
    return {
      key: b.key,
      label: b.label,
      range_label: b.range,
      current_expenses: cVal,
      previous_mtd_expenses: pVal,
      previous_full_expenses: pVal,
      current_cumulative: reached ? Math.round(curCum * 100) / 100 : null,
      previous_mtd_cumulative: Math.round(prevCum * 100) / 100,
      previous_full_cumulative: Math.round(prevCum * 100) / 100,
      delta_pct: reached ? pctChange(cVal, pVal) : null,
      reached,
    };
  });

  const rawMonthly: any[] =
    yearDashboard?.monthly_series || dashboard?.monthly_series || [];
  const monthlyComparison: MonthlyComparisonItem[] = rawMonthly
    .slice(-6)
    .map((item: any, idx: number, arr: any[]) => {
      const cVal = Number(item.expenses || 0);
      const pVal = idx > 0 ? Number(arr[idx - 1].expenses || 0) : prevExp;
      const mStr = String(item.month || "");
      return {
        month: mStr,
        label: mStr.length >= 7 ? `${mStr.slice(5, 7)}/${mStr.slice(2, 4)}` : mStr,
        current_expenses: cVal,
        previous_expenses: pVal,
        income: Number(item.income || 0),
        net: Number(item.net || 0),
        delta_amount: cVal - pVal,
        delta_pct: pctChange(cVal, pVal),
      };
    });

  const weeklySeries: WeeklyComparisonItem[] = ["Sem -3", "Sem -2", "Sem -1", "Atual"].map(
    (label, idx) => {
      const cVal = Math.round((curExp / 4) * 100) / 100;
      const pVal = Math.round((prevExp / 4) * 100) / 100;
      return {
        key: idx < 3 ? `W-${3 - idx}` : "W0",
        label,
        period_label: `Ciclo ${idx + 1}`,
        current_expenses: cVal,
        previous_expenses: pVal,
        current_income: Math.round((curInc / 4) * 100) / 100,
        delta_amount: cVal - pVal,
        delta_pct: pctChange(cVal, pVal),
      };
    },
  );

  let status: CostControlStatus = "no_baseline";
  let statusLabel = "Base em formação";
  let headline = `Acumulado no período: ${moneyFormatter(curExp)} (média de ${moneyFormatter(dailyCur)}/dia).`;
  if (prevExp > 0) {
    if (deltaPct != null && deltaPct <= -3) {
      status = "controlling";
      statusLabel = "Custos sob controle";
      headline = `Você reduziu os custos em ${Math.abs(deltaPct).toFixed(1)}% (-${moneyFormatter(Math.abs(deltaAmount))}) frente ao período anterior.`;
    } else if (deltaPct != null && deltaPct <= 5) {
      status = "stable";
      statusLabel = "Ritmo estável";
      headline = `Custos alinhados ao período anterior (${formatSignedPct(deltaPct)}).`;
    } else {
      status = "over_pace";
      statusLabel = "Acima do ritmo anterior";
      headline = `Custos estão ${formatSignedPct(deltaPct)} (+${moneyFormatter(Math.abs(deltaAmount))}) acima do período anterior.`;
    }
  }

  const insights: CostControlInsight[] = [];
  if (curExp > 0 || prevExp > 0) {
    insights.push({
      id: "burn_projection",
      tone: deltaAmount <= 0 ? "good" : "warning",
      title: "Ritmo diário e projeção do mês",
      detail: `Velocidade atual de ${moneyFormatter(dailyCur)}/dia. Projeção de fechamento em ${moneyFormatter(projectedExp)}.`,
      metric: `${moneyFormatter(dailyCur)}/dia`,
    });
  }
  if (curInc > 0 && curExp > 0) {
    const share = (curExp / curInc) * 100;
    insights.push({
      id: "income_commitment",
      tone: share <= 80 ? "good" : share <= 100 ? "warning" : "bad",
      title: "Comprometimento das entradas",
      detail: `As saídas representam ${share.toFixed(1)}% das entradas registradas no período.`,
      metric: `${share.toFixed(1)}% da renda`,
    });
  }

  return {
    status,
    status_label: statusLabel,
    headline,
    mtd: {
      reference_date: periodEndStr,
      current_month: periodEndStr.slice(0, 7),
      previous_month: `${curYearNum}-${String(prevMonthNum).padStart(2, "0")}`,
      day_of_month: dayOfMonth,
      days_in_month: daysInMonth,
      previous_days_compared: dayOfMonth,
      current_period_label: `01/${String(curMonthNum).padStart(2, "0")} a ${String(dayOfMonth).padStart(2, "0")}/${String(curMonthNum).padStart(2, "0")}`,
      previous_period_label: `01/${String(prevMonthNum).padStart(2, "0")} a ${String(dayOfMonth).padStart(2, "0")}/${String(prevMonthNum).padStart(2, "0")}`,
      current_expenses: curExp,
      previous_mtd_expenses: prevExp,
      previous_full_expenses: prevExp,
      current_income: curInc,
      previous_mtd_income: prevInc,
      delta_amount: deltaAmount,
      delta_pct: deltaPct,
      daily_burn_current: dailyCur,
      daily_burn_previous: dailyPrev,
      projected_month_expenses: projectedExp,
      projected_vs_previous_full_pct: projectedVsPrevPct,
      buckets,
    },
    weekly_series: weeklySeries,
    weekly_summary: {
      current_week_expenses: weeklySeries[3]?.current_expenses || 0,
      previous_week_expenses: weeklySeries[3]?.previous_expenses || 0,
      week_over_week_pct: weeklySeries[3]?.delta_pct ?? null,
      rolling_4w_expenses: curExp,
      prior_4w_expenses: prevExp,
      rolling_4w_change_pct: deltaPct,
      weekly_avg_current: curExp / 4,
      weekly_avg_previous: prevExp / 4,
    },
    monthly_comparison_series: monthlyComparison,
    category_drivers: [],
    insights,
  };
}
