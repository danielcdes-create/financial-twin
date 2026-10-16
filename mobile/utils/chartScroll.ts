type Scroller = {scrollTo?: (options: {x?: number; y?: number; animated?: boolean}) => void};

let offset = 0;
let locked = false;
let scroller: Scroller | null = null;

/** Page scroll of the commitments chart. The month screen restores this exact offset. */
export function recordChartOffset(value: number) {
  if (locked || !Number.isFinite(value) || value < 0) return;
  offset = value;
}

export function chartOffset(): number {
  return offset;
}

export function takeChartOffset(): number {
  locked = true;
  return offset;
}

export function bindChartScroller(node: Scroller | null) {
  scroller = node;
}

/** The month screen starts at the top. The saved offset stays put while the lock holds. */
export function showMonthAtTop() {
  scroller?.scrollTo?.({y: 0, animated: false});
}

export function restoreChartOffset() {
  scroller?.scrollTo?.({y: offset, animated: false});
  locked = false;
}
