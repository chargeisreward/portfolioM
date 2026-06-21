import { marked } from 'marked'

marked.setOptions({
  gfm: true,
  breaks: true,
})

export function mdToHtml(text) {
  if (!text) return ''
  return marked.parse(text)
}

export function injectPriceBadges(html, price, date, label = 'close') {
  if (price == null || date == null || !html) return html
  const priceText = typeof price === 'number' ? price.toFixed(2) : Number(price).toFixed(2)
  const badge = ` <span class="price-badge" title="最新收盘价">${label} ${priceText} (${date})</span>`
  // 在任意 "数字+元" 的价格描述后追加 badge；HTML 标签内不可能出现 "元"
  return html.replace(/(\d+\.?\d*)\s*元/g, (match) => `${match}${badge}`)
}

export function highlightNumbers(html) {
  if (!html) return html
  // 只高亮文本节点中的阿拉伯数字（含 +/-、千分位、小数、% / x / 倍），HTML 标签整体跳过；
  // 不碰日期/区间里的数字（如 2026-06-20、2026-2027）。
  return html.replace(
    /(<[^>]+>)|(?<![\d-])([+-]?\d[\d,]*(?:\.\d+)?(?:%|x|X|倍)?)(?![\d-])/g,
    (match, tag, num) => (tag !== undefined ? tag : `<span class="num-highlight">${num}</span>`)
  )
}
