import React from 'react'

export default function PriceBadge({ price, date, label = 'close' }) {
  if (price == null || date == null) return null
  const priceText = typeof price === 'number' ? price.toFixed(2) : price
  return (
    <span className="price-badge">
      {label} {priceText} ({date})
    </span>
  )
}
