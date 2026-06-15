import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { publicApi } from '../api'

export default function VerifyPage() {
  const { epicNo } = useParams()
  const navigate = useNavigate()
  const [voter, setVoter]     = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)

  useEffect(() => {
    if (!epicNo) return
    publicApi.verifyVoter(epicNo)
      .then((data) => setVoter(data))
      .catch((err) => setError(err.message || 'Voter not found'))
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
        <i className="bi bi-person-x" style={{ fontSize: 48, color: '#E53935' }} />
        <h2 style={{ fontSize: 20, fontWeight: 700 }}>Voter Not Found</h2>
        <p style={{ color: '#8696a0', fontSize: 14 }}>{error}</p>
        <button onClick={() => navigate('/')} style={{ background: '#E53935', color: '#fff', border: 'none', padding: '10px 24px', borderRadius: 20, fontFamily: 'inherit', fontSize: 14, fontWeight: 600, cursor: 'pointer' }}>
          Go Back
        </button>
      </div>
    )
  }

  const v = voter?.voter || voter || {}
  const hasCard = !!(v.card_url || voter?.card_url)
  const isVolunteer = voter?.is_volunteer || v.is_volunteer
  const isBoothAgent = voter?.is_booth_agent || v.is_booth_agent
  const photoUrl = v.photo_url || voter?.photo_url

  const fieldStyle = { display: 'flex', flexDirection: 'column', gap: 3 }
  const labelStyle = { fontSize: 10, color: '#8696a0', textTransform: 'uppercase', letterSpacing: 0.5, fontWeight: 600 }
  const valueStyle = { fontSize: 14, color: '#e9edef', fontWeight: 500 }

  return (
    <div style={{ minHeight: '100vh', background: '#08080C', padding: '24px 16px' }}>
      <div style={{ maxWidth: 520, margin: '0 auto' }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 24 }}>
          <img src="/newfavicon.png" alt="WTL" style={{ width: 44, height: 44, borderRadius: '50%' }} />
          <div>
            <div style={{ fontSize: 16, fontWeight: 800, color: '#e9edef', letterSpacing: 1 }}>WE THE LEADERS</div>
            <div style={{ fontSize: 11, color: '#43a047' }}>Voter Verification</div>
          </div>
          <button
            onClick={() => navigate(-1)}
            style={{ marginLeft: 'auto', background: 'none', border: '1px solid rgba(255,255,255,0.1)', color: '#8696a0', padding: '6px 14px', borderRadius: 8, fontSize: 13, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6 }}
          >
            <i className="bi bi-arrow-left" /> Back
          </button>
        </div>

        {/* Verified badge */}
        {hasCard && (
          <div style={{ background: 'rgba(46,125,50,0.12)', border: '1px solid rgba(46,125,50,0.25)', borderRadius: 10, padding: '10px 16px', marginBottom: 20, display: 'flex', alignItems: 'center', gap: 10 }}>
            <i className="bi bi-patch-check-fill" style={{ fontSize: 24, color: '#43a047' }} />
            <div>
              <div style={{ fontSize: 14, fontWeight: 700, color: '#66bb6a' }}>Verified Member</div>
              <div style={{ fontSize: 11, color: '#8696a0' }}>This person has generated their WTL ID card.</div>
            </div>
          </div>
        )}

        {/* Profile section */}
        <div style={{ background: '#121218', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 14, padding: 20, marginBottom: 16 }}>
          <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', marginBottom: 16 }}>
            {photoUrl ? (
              <img src={photoUrl} alt="Profile" style={{ width: 72, height: 90, objectFit: 'cover', borderRadius: 8, border: '2px solid rgba(229,57,53,0.3)', flexShrink: 0 }} />
            ) : (
              <div style={{ width: 72, height: 90, background: 'rgba(229,57,53,0.1)', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                <i className="bi bi-person" style={{ fontSize: 32, color: '#E53935' }} />
              </div>
            )}
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: '#e9edef' }}>{v.name || v.Name || 'N/A'}</div>
              {(v.father_name || v.FatherName) && (
                <div style={{ fontSize: 12, color: '#8696a0' }}>S/o, D/o: {v.father_name || v.FatherName}</div>
              )}
              {v.ptc_code && (
                <div style={{ display: 'inline-flex', alignItems: 'center', gap: 4, background: 'rgba(46,125,50,0.1)', border: '1px solid rgba(46,125,50,0.2)', borderRadius: 20, padding: '2px 10px', fontSize: 11, color: '#43a047', fontWeight: 600, width: 'fit-content' }}>
                  <i className="bi bi-qr-code" /> PTC: {v.ptc_code}
                </div>
              )}
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            {[
              { label: 'EPIC No',  value: v.epic_no || epicNo },
              { label: 'Assembly', value: v.assembly || v.AssemblyName },
              { label: 'District', value: v.district || v.DistrictName },
              { label: 'Part No',  value: v.part_no || v.PartNo },
              { label: 'Age',      value: v.age || v.Age },
              { label: 'Gender',   value: v.gender || v.Gender },
            ].filter((f) => f.value).map((f) => (
              <div key={f.label} style={fieldStyle}>
                <span style={labelStyle}>{f.label}</span>
                <span style={valueStyle}>{f.value}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Status tags */}
        {(isVolunteer || isBoothAgent) && (
          <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
            {isVolunteer && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'rgba(21,101,192,0.12)', border: '1px solid rgba(21,101,192,0.25)', borderRadius: 20, padding: '5px 12px', fontSize: 12, color: '#64b5f6', fontWeight: 600 }}>
                <i className="bi bi-hand-thumbs-up-fill" /> Volunteer
              </div>
            )}
            {isBoothAgent && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'rgba(106,27,154,0.12)', border: '1px solid rgba(106,27,154,0.25)', borderRadius: 20, padding: '5px 12px', fontSize: 12, color: '#ce93d8', fontWeight: 600 }}>
                <i className="bi bi-building-fill-check" /> Booth Agent {v.booth_no ? `(Booth ${v.booth_no})` : ''}
              </div>
            )}
          </div>
        )}

        {/* Card preview */}
        {hasCard && (v.card_url || voter?.card_url) && (
          <div style={{ background: '#121218', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 14, padding: 16, marginBottom: 16 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: '#8696a0', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 12 }}>
              <i className="bi bi-credit-card-2-front" /> Generated Card
            </div>
            <img
              src={v.card_url || voter?.card_url}
              alt="Card"
              style={{ width: '100%', maxWidth: 260, borderRadius: 8, boxShadow: '0 4px 20px rgba(0,0,0,0.4)', display: 'block' }}
            />
          </div>
        )}

        <div style={{ textAlign: 'center' }}>
          <a href={`/card/${epicNo}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, background: 'rgba(229,57,53,0.12)', border: '1px solid rgba(229,57,53,0.25)', color: '#ef9a9a', padding: '9px 20px', borderRadius: 20, fontSize: 13, fontWeight: 600, textDecoration: 'none' }}>
            <i className="bi bi-eye" /> View Full Card
          </a>
        </div>
      </div>
    </div>
  )
}
