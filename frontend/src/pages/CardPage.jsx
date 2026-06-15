import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { publicApi } from '../api'

const getDownloadUrl = (url, epicNo) => {
  if (url && url.includes('/upload/')) {
    return url.replace('/upload/', `/upload/fl_attachment:${epicNo}_WTL_Card/`)
  }
  return url
}

export default function CardPage() {
  const { epicNo } = useParams()
  const navigate = useNavigate()
  const [card, setCard]       = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)
  const [isFlipped, setIsFlipped] = useState(false)

  useEffect(() => {
    if (!epicNo) return
    publicApi.getCardData(epicNo)
      .then((data) => setCard(data))
      .catch((err) => setError(err.message || 'Card not found'))
      .finally(() => setLoading(false))
  }, [epicNo])

  if (loading) {
    return (
      <div className="page-loader">
        <div className="spinner-border text-danger" role="status" />
      </div>
    )
  }

  if (error) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 16, background: '#08080C', color: '#e9edef', padding: 24, textAlign: 'center' }}>
        <i className="bi bi-exclamation-circle" style={{ fontSize: 48, color: '#E53935' }} />
        <h2 style={{ fontSize: 20, fontWeight: 700 }}>Card Not Found</h2>
        <p style={{ color: '#8696a0', fontSize: 14 }}>{error}</p>
        <button onClick={() => navigate('/')} style={{ background: '#E53935', color: '#fff', border: 'none', padding: '10px 24px', borderRadius: 20, fontFamily: 'inherit', fontSize: 14, fontWeight: 600, cursor: 'pointer' }}>
          Go Back
        </button>
      </div>
    )
  }

  const cardData = card?.card || card || {}
  const downloadUrl = getDownloadUrl(cardData.combined_url || cardData.card_url, epicNo)

  return (
    <div style={{
      minHeight: '100vh',
      background: '#08080C',
      backgroundImage: 'url(/bg.png)',
      backgroundSize: 'cover',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '20px 16px',
      gap: 20,
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
        <img src="/newfavicon.png" alt="WTL" style={{ width: 44, height: 44, borderRadius: '50%', objectFit: 'cover' }} />
        <div>
          <div style={{ fontSize: 16, fontWeight: 800, color: '#e9edef', letterSpacing: 1 }}>WE THE LEADERS</div>
          <div style={{ fontSize: 11, color: '#43a047' }}>Lead the Change</div>
        </div>
      </div>

      {/* Card viewer */}
      <div style={{ perspective: '1200px', width: '100%', maxWidth: 340, cursor: 'pointer' }} onClick={() => setIsFlipped((f) => !f)}>
        <div style={{
          position: 'relative',
          width: '100%',
          paddingBottom: '62%',
          transition: 'transform 0.65s cubic-bezier(0.4,0.2,0.2,1)',
          transformStyle: 'preserve-3d',
          transform: isFlipped ? 'rotateY(180deg)' : 'none',
        }}>
          {/* Front */}
          <div style={{
            position: 'absolute', inset: 0, backfaceVisibility: 'hidden', WebkitBackfaceVisibility: 'hidden',
            borderRadius: 10, overflow: 'hidden', boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
          }}>
            {cardData.card_url
              ? <img src={cardData.card_url} alt="Card Front" style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
              : <div style={{ width: '100%', height: '100%', background: '#1f2c34', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#8696a0', fontSize: 13 }}>No card image</div>
            }
          </div>
          {/* Back */}
          <div style={{
            position: 'absolute', inset: 0, backfaceVisibility: 'hidden', WebkitBackfaceVisibility: 'hidden',
            borderRadius: 10, overflow: 'hidden', boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
            transform: 'rotateY(180deg)',
          }}>
            {cardData.back_url || cardData.card_url
              ? <img src={cardData.back_url || cardData.card_url} alt="Card Back" style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
              : <div style={{ width: '100%', height: '100%', background: '#1f2c34', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#8696a0', fontSize: 13 }}>Back</div>
            }
          </div>
        </div>
      </div>

      <p style={{ fontSize: 11, color: '#8696a0', margin: 0, display: 'flex', alignItems: 'center', gap: 4 }}>
        <i className="bi bi-arrow-repeat" /> Tap card to flip
      </p>

      {/* EPIC info */}
      {epicNo && (
        <div style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 8, padding: '8px 16px', fontSize: 13, color: '#8696a0' }}>
          EPIC: <strong style={{ color: '#e9edef' }}>{epicNo}</strong>
          {cardData.ptc_code && <span style={{ marginLeft: 12 }}>PTC: <strong style={{ color: '#43a047' }}>{cardData.ptc_code}</strong></span>}
        </div>
      )}

      {/* Action buttons */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', justifyContent: 'center' }}>
        <a
          href={`/verify/${epicNo}`}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6, background: 'rgba(21,101,192,0.15)', border: '1px solid rgba(21,101,192,0.3)', color: '#64b5f6', padding: '8px 18px', borderRadius: 20, fontSize: 13, fontWeight: 600, textDecoration: 'none' }}
        >
          <i className="bi bi-patch-check-fill" /> Verify
        </a>
        {downloadUrl && (
          <a
            href={downloadUrl}
            target="_blank"
            rel="noreferrer"
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6, background: 'rgba(229,57,53,0.15)', border: '1px solid rgba(229,57,53,0.3)', color: '#ef9a9a', padding: '8px 18px', borderRadius: 20, fontSize: 13, fontWeight: 600, textDecoration: 'none' }}
          >
            <i className="bi bi-download" /> Download
          </a>
        )}
        <button
          onClick={() => navigate('/')}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)', color: '#e9edef', padding: '8px 18px', borderRadius: 20, fontSize: 13, fontWeight: 600, cursor: 'pointer' }}
        >
          <i className="bi bi-house" /> Home
        </button>
      </div>
    </div>
  )
}
