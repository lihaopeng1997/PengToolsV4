import { ref, computed, onMounted, onUnmounted } from 'vue'
import rawQuotes from '../../../../resources/ui/daily-quotes.json'

export interface DailyQuoteItem {
  id: string
  text: string
  source: string
  author: string
}

export const DAILY_QUOTES: DailyQuoteItem[] = rawQuotes as DailyQuoteItem[]

const WEEKDAY_NAMES = ['日', '一', '二', '三', '四', '五', '六']

/**
 * Calculate UTC day ordinal from local calendar year, month, date.
 * Eliminates daylight saving time hour shifts while strictly adhering
 * to local calendar date (avoids UTC boundary mismatch).
 */
export function getDayOrdinal(date: Date): number {
  return Math.floor(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / 86400000)
}

/**
 * Format local date line, e.g. "9 月 8 日，星期二"
 */
export function formatLocalDateLine(date: Date): string {
  const m = date.getMonth() + 1
  const d = date.getDate()
  const w = WEEKDAY_NAMES[date.getDay()]
  return `${m} 月 ${d} 日，星期${w}`
}

export function useDailyQuote(getNow: () => Date = () => new Date()) {
  const currentDate = ref<Date>(getNow())
  const offset = ref(0)
  let timerId: ReturnType<typeof setInterval> | null = null

  function checkDate() {
    const now = getNow()
    const prevOrdinal = getDayOrdinal(currentDate.value)
    const newOrdinal = getDayOrdinal(now)
    if (newOrdinal !== prevOrdinal) {
      currentDate.value = now
    }
  }

  function handleVisibilityChange() {
    if (typeof document !== 'undefined' && !document.hidden) {
      checkDate()
    }
  }

  onMounted(() => {
    // 60-second periodic midnight crossing check
    timerId = setInterval(checkDate, 60000)

    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', handleVisibilityChange)
    }
  })

  onUnmounted(() => {
    if (timerId !== null) {
      clearInterval(timerId)
      timerId = null
    }
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  })

  const dayOrdinal = computed(() => getDayOrdinal(currentDate.value))

  const dateText = computed(() => formatLocalDateLine(currentDate.value))

  const quoteIndex = computed(() => {
    const total = DAILY_QUOTES.length
    if (total === 0) return 0
    const raw = (dayOrdinal.value + offset.value) % total
    return (raw + total) % total
  })

  const quote = computed<DailyQuoteItem | null>(() => {
    return DAILY_QUOTES[quoteIndex.value] || null
  })

  function nextQuote() {
    offset.value++
  }

  return {
    currentDate,
    dayOrdinal,
    dateText,
    quote,
    offset,
    nextQuote,
    checkDate
  }
}
