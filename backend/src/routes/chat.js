/**
 * Chatbot API routes — faithful port of app.py's /api/chat/* endpoints.
 */
const express  = require('express');
const router   = express.Router();
const multer   = require('multer');
const { v4: uuidv4 } = require('uuid');
const crypto   = require('crypto');

const { validateMobile, validateEpic, validatePin, validateOtp } = require('../utils/validators');
const { hashPin, verifyPin } = require('../utils/security');
const { sendOtp } = require('../services/smsService');
const { uploadPhoto, uploadCard, uploadBackCard, uploadCombinedCard } = require('../services/cloudinaryService');
const { generateCard, generateBackCard, generateCombinedCard } = require('../services/cardGenerator');
const { chatOtpLimiter, chatGenerateCardLimiter, chatValidateEpicLimiter } = require('../middleware/rateLimiter');
const { getDb, getVoterDb, findVoterByEpic } = require('../db');

// ── Multer (memory storage, 10 MB limit) ─────────────────────────
const upload = multer({
  storage: multer.memoryStorage(),
  limits:  { fileSize: 10 * 1024 * 1024 },
  fileFilter: (req, file, cb) => {
    if (/image\/(png|jpe?g|bmp)/.test(file.mimetype)) cb(null, true);
    else cb(new Error('Only image files (PNG/JPG/BMP) are allowed'));
  },
});

// ── In-memory job store for async card generation ─────────────────
const jobs = new Map();
const JOB_TTL = 60 * 60 * 1000; // 1 hour

function getJob(jobId) {
  const job = jobs.get(jobId);
  if (!job) return null;
  if (Date.now() - job.createdAt > JOB_TTL) { jobs.delete(jobId); return null; }
  return job;
}

/**
 * normaliseVoter — maps DB1 schema to a consistent shape used by
 * the frontend chatbot and card generator.
 *
 * DB1 actual fields:
 *   EPIC_NO, VOTER_NAME, ASSEMBLY_NO, ASSEMBLY_NAME, DISTRICT,
 *   GENDER, MOBILE_NUMBER, ID
 */
function normaliseVoter(doc) {
  if (!doc) return null;
  return {
    epic_no:       doc.EPIC_NO        || '',
    EPIC_NO:       doc.EPIC_NO        || '',
    name:          doc.VOTER_NAME     || '',
    voter_name:    doc.VOTER_NAME     || '',
    VOTER_NAME:    doc.VOTER_NAME     || '',
    assembly_no:   String(doc.ASSEMBLY_NO  || ''),
    assembly_name: doc.ASSEMBLY_NAME  || '',
    ASSEMBLY_NAME: doc.ASSEMBLY_NAME  || '',
    ASSEMBLY_NO:   String(doc.ASSEMBLY_NO  || ''),
    district:      doc.DISTRICT       || '',
    DISTRICT:      doc.DISTRICT       || '',
    DISTRICT_NAME: doc.DISTRICT       || '',
    gender:        doc.GENDER         || '',
    GENDER:        doc.GENDER         || '',
    mobile:        doc.MOBILE_NUMBER  || '',
    MOBILE_NO:     doc.MOBILE_NUMBER  || '',
    // Fields that don't exist in this DB — blank defaults
    age:           '',
    part_no:       '',
    section_no:    '',
    house_no:      '',
    dob:           '',
    relation_name: '',
  };
}

// ── Helpers ───────────────────────────────────────────────────────
function nowUTC() { return new Date(); }

function generatePtcCode() {
  return 'PTC-' + crypto.randomBytes(4).toString('hex').toUpperCase().slice(0, 7);
}

function genOtp() {
  return String(crypto.randomInt(100000, 1000000));
}

// ────────────────────────────────────────────────────────────────
//  POST /send-otp
// ────────────────────────────────────────────────────────────────
router.post('/send-otp', chatOtpLimiter, async (req, res) => {
  try {
    const { valid, value: mobile } = validateMobile((req.body.mobile || '').trim());
    if (!valid) return res.status(400).json({ success: false, message: mobile });

    const db  = getDb();
    const doc = await db.collection('otp_sessions').findOne({ mobile }, { projection: { created_at: 1 } });

    // 60-second cooldown between OTP requests
    if (doc?.created_at) {
      const elapsed = (Date.now() - new Date(doc.created_at).getTime()) / 1000;
      if (elapsed < 60) {
        const wait = Math.ceil(60 - elapsed);
        return res.status(429).json({ success: false, message: `Please wait ${wait}s before requesting another OTP.` });
      }
    }

    const otp    = genOtp();
    const result = await sendOtp(mobile, otp);
    if (!result.success) {
      return res.status(500).json({ success: false, message: 'Could not send OTP. Please try again.' });
    }

    await db.collection('otp_sessions').updateOne(
      { mobile },
      { $set: { otp, created_at: nowUTC(), verified: false, purpose: null } },
      { upsert: true }
    );

    return res.json({ success: true });
  } catch (err) {
    console.error('send-otp error:', err);
    return res.status(500).json({ success: false, message: 'Server error' });
  }
});

// ────────────────────────────────────────────────────────────────
//  POST /verify-otp
// ────────────────────────────────────────────────────────────────
router.post('/verify-otp', async (req, res) => {
  try {
    const { valid: vm, value: mobile } = validateMobile((req.body.mobile || '').trim());
    if (!vm) return res.status(400).json({ success: false, message: mobile });

    const { valid: vo, value: otp } = validateOtp((req.body.otp || '').trim());
    if (!vo) return res.status(400).json({ success: false, message: otp });

    const db  = getDb();
    const doc = await db.collection('otp_sessions').findOne({ mobile });

    if (!doc || doc.otp !== otp) {
      return res.status(400).json({ success: false, message: 'Invalid OTP' });
    }

    // 5-minute expiry
    const elapsed = (Date.now() - new Date(doc.created_at).getTime()) / 1000;
    if (elapsed > 300) {
      return res.status(400).json({ success: false, message: 'OTP expired. Please request a new one.' });
    }

    await db.collection('otp_sessions').updateOne({ mobile }, { $set: { verified: true } });
    req.session.verified_mobile = mobile;
    req.session.cookie.maxAge   = 86400 * 1000;

    // Check if user already has a card
    const stat   = await db.collection('generation_stats').findOne({ auth_mobile: mobile });
    const genDoc = await db.collection('generated_voters').findOne(
      { MOBILE_NO: mobile }, { sort: { generated_at: -1 } }
    );

    if ((stat && stat.card_url) || (genDoc && genDoc.card_url)) {
      const s = stat || {};
      const g = genDoc || {};
      const name = `${g.FM_NAME_EN || ''} ${g.LASTNAME_EN || ''}`.trim();
      return res.json({
        success:    true,
        has_card:   true,
        epic_no:    s.epic_no || g.EPIC_NO || '',
        card_url:   s.card_url || g.card_url || '',
        voter_name: name,
        photo_url:  g.photo_url || '',
      });
    }

    return res.json({ success: true, has_card: false });
  } catch (err) {
    console.error('verify-otp error:', err);
    return res.status(500).json({ success: false, message: 'Server error' });
  }
});

// ────────────────────────────────────────────────────────────────
//  POST /check-mobile
// ────────────────────────────────────────────────────────────────
router.post('/check-mobile', async (req, res) => {
  try {
    const mobile = String(req.body.mobile || '').trim();
    if (!mobile || mobile.length !== 10) {
      return res.status(400).json({ success: false, message: 'Invalid mobile number' });
    }

    const db     = getDb();
    const stat   = await db.collection('generation_stats').findOne({ auth_mobile: mobile });
    const genDoc = await db.collection('generated_voters').findOne(
      { MOBILE_NO: mobile }, { sort: { generated_at: -1 } }
    );

    const hasCard = Boolean((stat && stat.card_url) || (genDoc && genDoc.card_url));

    if (hasCard) {
      const s      = stat || {};
      const g      = genDoc || {};
      const hasPin = Boolean(s.secret_pin || g.secret_pin);
      const result = { success: true, has_card: true, has_pin: hasPin };

      if (!hasPin) {
        const name = `${g.FM_NAME_EN || ''} ${g.LASTNAME_EN || ''}`.trim();
        result.epic_no    = s.epic_no || g.EPIC_NO || '';
        result.card_url   = s.card_url || g.card_url || '';
        result.voter_name = name;
        result.photo_url  = g.photo_url || '';
      }
      return res.json(result);
    }

    return res.json({ success: true, has_card: false, has_pin: false });
  } catch (err) {
    console.error('check-mobile error:', err);
    return res.status(500).json({ success: false, message: 'Server error' });
  }
});

// ────────────────────────────────────────────────────────────────
//  POST /verify-pin
// ────────────────────────────────────────────────────────────────
router.post('/verify-pin', async (req, res) => {
  try {
    const { valid: vm, value: mobile } = validateMobile((req.body.mobile || '').trim());
    if (!vm) return res.status(400).json({ success: false, message: mobile });

    const { valid: vp, value: pin } = validatePin((req.body.pin || '').trim());
    if (!vp) return res.status(400).json({ success: false, message: pin });

    const db   = getDb();
    const stat = await db.collection('generation_stats').findOne({ auth_mobile: mobile });

    if (!stat || !stat.secret_pin) {
      return res.status(404).json({ success: false, message: 'No PIN found for this mobile.' });
    }
    if (!verifyPin(pin, stat.secret_pin)) {
      return res.status(400).json({ success: false, message: 'Invalid PIN. Please try again.' });
    }

    const genDoc = await db.collection('generated_voters').findOne({ MOBILE_NO: mobile });
    const name   = genDoc ? `${genDoc.FM_NAME_EN || ''} ${genDoc.LASTNAME_EN || ''}`.trim() : '';

    return res.json({
      success:    true,
      has_card:   true,
      epic_no:    stat.epic_no || '',
      card_url:   stat.card_url || '',
      voter_name: name,
      photo_url:  genDoc?.photo_url || '',
    });
  } catch (err) {
    console.error('verify-pin error:', err);
    return res.status(500).json({ success: false, message: 'Server error' });
  }
});

// ────────────────────────────────────────────────────────────────
//  POST /forgot-pin
// ────────────────────────────────────────────────────────────────
router.post('/forgot-pin', chatOtpLimiter, async (req, res) => {
  try {
    const mobile = String(req.body.mobile || '').trim();
    if (!mobile || mobile.length !== 10) {
      return res.status(400).json({ success: false, message: 'Invalid mobile number' });
    }

    const db       = getDb();
    const hasAcct  = (await db.collection('generation_stats').findOne({ auth_mobile: mobile })) ||
                     (await db.collection('generated_voters').findOne({ MOBILE_NO: mobile }));

    if (!hasAcct) {
      return res.status(404).json({ success: false, message: 'No account found for this mobile.' });
    }

    // 60-second cooldown
    const existing = await db.collection('otp_sessions').findOne({ mobile }, { projection: { created_at: 1 } });
    if (existing?.created_at) {
      const elapsed = (Date.now() - new Date(existing.created_at).getTime()) / 1000;
      if (elapsed < 60) {
        const wait = Math.ceil(60 - elapsed);
        return res.status(429).json({ success: false, message: `Please wait ${wait}s.` });
      }
    }

    const otp    = genOtp();
    const result = await sendOtp(mobile, otp);
    if (!result.success) {
      return res.status(500).json({ success: false, message: 'Could not send OTP. Please try again.' });
    }

    await db.collection('otp_sessions').updateOne(
      { mobile },
      { $set: { otp, created_at: nowUTC(), verified: false, purpose: 'pin_reset' } },
      { upsert: true }
    );

    return res.json({ success: true });
  } catch (err) {
    console.error('forgot-pin error:', err);
    return res.status(500).json({ success: false, message: 'Server error' });
  }
});

// ────────────────────────────────────────────────────────────────
//  POST /verify-forgot-otp
// ────────────────────────────────────────────────────────────────
router.post('/verify-forgot-otp', async (req, res) => {
  try {
    const mobile = String(req.body.mobile || '').trim();
    const otp    = String(req.body.otp    || '').trim();
    if (!mobile || !otp) {
      return res.status(400).json({ success: false, message: 'Mobile and OTP required' });
    }

    const db  = getDb();
    const doc = await db.collection('otp_sessions').findOne({ mobile });

    if (!doc || doc.otp !== otp) {
      return res.status(400).json({ success: false, message: 'Invalid OTP' });
    }

    const elapsed = (Date.now() - new Date(doc.created_at).getTime()) / 1000;
    if (elapsed > 300) {
      return res.status(400).json({ success: false, message: 'OTP expired.' });
    }

    return res.json({ success: true });
  } catch (err) {
    console.error('verify-forgot-otp error:', err);
    return res.status(500).json({ success: false, message: 'Server error' });
  }
});

// ────────────────────────────────────────────────────────────────
//  POST /reset-pin
// ────────────────────────────────────────────────────────────────
router.post('/reset-pin', async (req, res) => {
  try {
    const { valid: vm, value: mobile } = validateMobile((req.body.mobile || '').trim());
    if (!vm) return res.status(400).json({ success: false, message: mobile });

    const { valid: vo, value: otp } = validateOtp((req.body.otp || '').trim());
    if (!vo) return res.status(400).json({ success: false, message: otp });

    const db  = getDb();
    const doc = await db.collection('otp_sessions').findOne({ mobile });

    if (!doc || doc.otp !== otp) {
      return res.status(400).json({ success: false, message: 'Invalid OTP' });
    }

    const elapsed = (Date.now() - new Date(doc.created_at).getTime()) / 1000;
    if (elapsed > 300) {
      return res.status(400).json({ success: false, message: 'OTP expired.' });
    }

    const { valid: vp, value: newPin } = validatePin((req.body.new_pin || '').trim());
    if (!vp) return res.status(400).json({ success: false, message: newPin });

    const hashed = hashPin(newPin);
    await db.collection('generation_stats').updateOne({ auth_mobile: mobile }, { $set: { secret_pin: hashed } });
    await db.collection('generated_voters').updateMany({ MOBILE_NO: mobile },   { $set: { secret_pin: hashed } });
    await db.collection('otp_sessions').deleteOne({ mobile });

    const stat   = await db.collection('generation_stats').findOne({ auth_mobile: mobile });
    const genDoc = await db.collection('generated_voters').findOne({ MOBILE_NO: mobile });
    const name   = genDoc ? `${genDoc.FM_NAME_EN || ''} ${genDoc.LASTNAME_EN || ''}`.trim() : '';

    return res.json({
      success:    true,
      has_card:   true,
      epic_no:    (stat || {}).epic_no   || '',
      card_url:   (stat || {}).card_url  || '',
      voter_name: name,
      photo_url:  genDoc?.photo_url || '',
    });
  } catch (err) {
    console.error('reset-pin error:', err);
    return res.status(500).json({ success: false, message: 'Server error' });
  }
});

// ────────────────────────────────────────────────────────────────
//  POST /set-pin
// ────────────────────────────────────────────────────────────────
router.post('/set-pin', async (req, res) => {
  try {
    const { valid: vm, value: mobile } = validateMobile((req.body.mobile || '').trim());
    if (!vm) return res.status(400).json({ success: false, message: mobile });

    const { valid: vp, value: pin } = validatePin((req.body.pin || '').trim());
    if (!vp) return res.status(400).json({ success: false, message: pin });

    const epicNo = String(req.body.epic_no || '').trim().toUpperCase();

    const hashed = hashPin(pin);
    const db     = getDb();

    if (epicNo) {
      await db.collection('generation_stats').updateOne(
        { epic_no: epicNo },
        { $set: { secret_pin: hashed, auth_mobile: mobile }, $setOnInsert: { epic_no: epicNo } },
        { upsert: true }
      );
    } else {
      await db.collection('generation_stats').updateOne({ auth_mobile: mobile }, { $set: { secret_pin: hashed } });
    }

    await db.collection('generated_voters').updateMany({ MOBILE_NO: mobile }, { $set: { secret_pin: hashed } });

    return res.json({ success: true });
  } catch (err) {
    console.error('set-pin error:', err);
    return res.status(500).json({ success: false, message: 'Server error' });
  }
});

// ────────────────────────────────────────────────────────────────
//  POST /validate-epic
// ────────────────────────────────────────────────────────────────
router.post('/validate-epic', chatValidateEpicLimiter, async (req, res) => {
  try {
    const raw = String(req.body.epic_no || req.body.epic || '').trim().toUpperCase();
    const { valid, value: epicNo } = validateEpic(raw);
    if (!valid) return res.status(400).json({ success: false, message: epicNo });

    // EPIC lookup from DB1 across all ass_* collections
    const doc = await findVoterByEpic(epicNo);
    if (!doc) {
      return res.status(404).json({ success: false, message: 'EPIC Number not found. Please check and try again.' });
    }

    // Normalise to a consistent shape the frontend understands
    const voter = normaliseVoter(doc);
    return res.json({ success: true, voter });
  } catch (err) {
    console.error('validate-epic error:', err);
    return res.status(500).json({ success: false, message: 'Server error' });
  }
});

// ────────────────────────────────────────────────────────────────
//  POST /generate-card  (with photo upload)
// ────────────────────────────────────────────────────────────────
router.post('/generate-card', chatGenerateCardLimiter, upload.single('photo'), async (req, res) => {
  try {
    const rawEpic = String(req.body.epic_no || req.body.epic || '').trim().toUpperCase();
    const { valid: ve, value: epicNo } = validateEpic(rawEpic);
    if (!ve) return res.status(400).json({ success: false, message: epicNo });

    // Photo is required
    if (!req.file) {
      return res.status(400).json({ success: false, message: 'Please upload your passport photo.' });
    }

    const db    = getDb();
    // EPIC lookup from DB1 — search across all ass_* collections
    const rawVoter = await findVoterByEpic(epicNo);
    if (!rawVoter) {
      return res.status(404).json({ success: false, message: 'EPIC Number not found.' });
    }
    const voter = normaliseVoter(rawVoter);

    // Use mobile from session or body
    const mobile = req.session.verified_mobile ||
                   String(req.body.mobile || '').trim() || '';

    const photoBuffer = req.file.buffer;

    // ── Process synchronously ─────────────────────────────────────
    try {
      const ptcCode   = generatePtcCode();
      const verifyUrl = `${require('../config').baseUrl}/verify/${epicNo}`;

      const voterData = {
        epic_no:       voter.epic_no,
        name:          voter.name,
        assembly_name: voter.assembly_name,
        district:      voter.district,
        ptc_code:      ptcCode,
        verify_url:    verifyUrl,
        VOTER_NAME:    voter.name,
        ASSEMBLY_NAME: voter.assembly_name,
        DISTRICT_NAME: voter.district,
        DISTRICT:      voter.district,
        EPIC_NO:       voter.epic_no,
        ASSEMBLY_NO:   voter.assembly_no,
      };

      // Upload photo
      let photoUrl = '';
      try {
        photoUrl = await uploadPhoto(photoBuffer, epicNo);
      } catch (e) {
        console.error('Photo upload failed:', e.message);
      }

      // Generate front card
      const frontBuffer = await generateCard(voterData, photoBuffer);

      // Upload front card
      const cardUrl = await uploadCard(frontBuffer, epicNo);

      // Generate + upload back card
      let backUrl     = '';
      let combinedUrl = cardUrl;
      try {
        const backBuffer     = await generateBackCard(voterData);
        backUrl              = await uploadBackCard(backBuffer, epicNo);
        const combinedBuffer = await generateCombinedCard(frontBuffer, backBuffer);
        combinedUrl          = await uploadCombinedCard(combinedBuffer, epicNo);
      } catch (e) {
        console.warn('Back/combined card error:', e.message);
      }

      const now = nowUTC();

      // Upsert generated_voters into DB2
      await db.collection('generated_voters').updateOne(
        { EPIC_NO: epicNo },
        {
          $set: {
            EPIC_NO:       epicNo,
            ptc_code:      ptcCode,
            photo_url:     photoUrl,
            card_url:      cardUrl,
            back_url:      backUrl,
            combined_url:  combinedUrl,
            generated_at:  now,
            VOTER_NAME:    voter.name,
            ASSEMBLY_NAME: voter.assembly_name,
            DISTRICT_NAME: voter.district,
            ASSEMBLY_NO:   voter.assembly_no,
            ...(mobile ? { MOBILE_NO: mobile } : {}),
          },
          $setOnInsert: { created_at: now },
        },
        { upsert: true }
      );

      // Upsert generation_stats
      await db.collection('generation_stats').updateOne(
        { epic_no: epicNo },
        {
          $set:         { card_url: cardUrl, back_url: backUrl, combined_url: combinedUrl, photo_url: photoUrl, last_generated: now },
          $inc:         { count: 1 },
          $setOnInsert: { epic_no: epicNo },
        },
        { upsert: true }
      );

      const voterName = voter.name;

      return res.json({
        success:      true,
        card_url:     cardUrl,
        back_url:     backUrl,
        combined_url: combinedUrl,
        photo_url:    photoUrl,
        epic_no:      epicNo,
        voter_name:   voterName,
        ptc_code:     ptcCode,
        message:      'Card generated successfully',
      });

    } catch (genErr) {
      console.error(`Card generation error for ${epicNo}:`, genErr);
      return res.status(500).json({ success: false, message: 'Card generation failed. Please try again.' });
    }

  } catch (err) {
    console.error('generate-card error:', err);
    return res.status(500).json({ success: false, message: 'Server error' });
  }
});

// ────────────────────────────────────────────────────────────────
//  GET /card-status/:jobId
// ────────────────────────────────────────────────────────────────
router.get('/card-status/:jobId', (req, res) => {
  const job = getJob(req.params.jobId);
  if (!job) return res.status(404).json({ status: 'error', message: 'Job not found or expired' });
  return res.json({ status: job.status, ...job });
});

// ────────────────────────────────────────────────────────────────
//  GET /profile/:epicNo  (frontend calls GET /api/profile/:epicNo)
// ────────────────────────────────────────────────────────────────
router.get('/profile/:epicNo', async (req, res) => {
  try {
    const epicNo = String(req.params.epicNo || '').trim().toUpperCase();
    const mobile = String(req.query.mobile || '').trim();
    if (!epicNo) return res.status(400).json({ success: false, message: 'EPIC required' });

    const db       = getDb();
    const rawVoter = await findVoterByEpic(epicNo);
    if (!rawVoter) return res.status(404).json({ success: false, message: 'Voter not found' });

    const voter  = normaliseVoter(rawVoter);
    const genDoc = await db.collection('generated_voters').findOne({ EPIC_NO: epicNo }) || {};
    const stat   = await db.collection('generation_stats').findOne({ epic_no: epicNo }) || {};
    const mob    = stat.auth_mobile || mobile || '';

    return res.json({
      success:            true,
      name:               voter.name,
      epic_no:            epicNo,
      assembly:           voter.assembly_name,
      district:           voter.district,
      ptc_code:           genDoc.ptc_code    || '',
      card_url:           stat.card_url      || genDoc.card_url      || '',
      back_url:           stat.back_url      || genDoc.back_url      || '',
      combined_url:       stat.combined_url  || genDoc.combined_url  || '',
      photo_url:          stat.photo_url     || genDoc.photo_url     || '',
      auth_mobile_masked: mob.length >= 4 ? `****${mob.slice(-4)}` : '',
    });
  } catch (err) {
    console.error('profile error:', err);
    return res.status(500).json({ success: false, message: 'Server error' });
  }
});

// ────────────────────────────────────────────────────────────────
//  GET /booth/:epicNo
// ────────────────────────────────────────────────────────────────
router.get('/booth/:epicNo', async (req, res) => {
  try {
    const epicNo   = String(req.params.epicNo || '').trim().toUpperCase();
    const rawVoter = await findVoterByEpic(epicNo);
    if (!rawVoter) return res.status(404).json({ success: false, message: 'Voter not found' });
    const voter = normaliseVoter(rawVoter);

    return res.json({
      success:         true,
      assembly_name:   voter.assembly_name,
      assembly_no:     voter.assembly_no,
      district:        voter.district,
      part_no:         voter.part_no || '',
      polling_station: '',
    });
  } catch (err) {
    console.error('booth error:', err);
    return res.status(500).json({ success: false, message: 'Server error' });
  }
});

// ────────────────────────────────────────────────────────────────
//  GET /referral-link/:ptcCode  (frontend: GET /api/referral-link/:ptcCode)
// ────────────────────────────────────────────────────────────────
router.get('/referral-link/:ptcCode', async (req, res) => {
  try {
    const ptcCode = String(req.params.ptcCode || '').trim();
    if (!ptcCode) return res.status(400).json({ success: false, message: 'PTC code required' });

    const db  = getDb();
    const doc = await db.collection('generated_voters').findOne(
      { ptc_code: ptcCode },
      { projection: { referral_id: 1, referral_link: 1 } }
    );

    if (!doc) return res.status(404).json({ success: false, message: 'Member not found' });

    if (doc.referral_id) {
      return res.json({ success: true, referral_id: doc.referral_id, referral_link: doc.referral_link });
    }

    const rid  = 'REF-' + crypto.randomBytes(4).toString('hex').toUpperCase();
    const link = `${require('../config').baseUrl}/refer/${ptcCode}/${rid}`;

    await db.collection('generated_voters').updateOne(
      { ptc_code: ptcCode },
      { $set: { referral_id: rid, referral_link: link } }
    );

    return res.json({ success: true, referral_id: rid, referral_link: link });
  } catch (err) {
    console.error('referral-link error:', err);
    return res.status(500).json({ success: false, message: 'Server error' });
  }
});

// ────────────────────────────────────────────────────────────────
//  GET /my-members/:ptcCode  (frontend: GET /api/my-members/:ptcCode)
// ────────────────────────────────────────────────────────────────
router.get('/my-members/:ptcCode', async (req, res) => {
  try {
    const ptcCode = String(req.params.ptcCode || '').trim();
    if (!ptcCode) return res.status(400).json({ success: false, message: 'PTC code required' });

    const db      = getDb();
    const members = await db.collection('generated_voters')
      .find(
        { referred_by_ptc: ptcCode },
        { projection: { FM_NAME_EN: 1, LASTNAME_EN: 1, EPIC_NO: 1, ptc_code: 1, generated_at: 1 } }
      )
      .sort({ generated_at: -1 })
      .limit(50)
      .toArray();

    const result = members.map(m => ({
      name:     `${m.FM_NAME_EN || ''} ${m.LASTNAME_EN || ''}`.trim(),
      epic_no:  m.EPIC_NO   || '',
      ptc_code: m.ptc_code  || '',
    }));

    return res.json({ success: true, members: result, total: result.length });
  } catch (err) {
    console.error('my-members error:', err);
    return res.status(500).json({ success: false, message: 'Server error' });
  }
});

// ────────────────────────────────────────────────────────────────
//  POST /request-volunteer
// ────────────────────────────────────────────────────────────────
router.post('/request-volunteer', async (req, res) => {
  try {
    const ptcCode = String(req.body.ptc_code || '').trim();
    const epicNo  = String(req.body.epic_no  || '').trim().toUpperCase();

    if (!ptcCode) return res.status(400).json({ success: false, message: 'PTC code required' });

    const db  = getDb();
    const gen = await db.collection('generated_voters').findOne({ ptc_code: ptcCode }) || {};
    const name = `${gen.FM_NAME_EN || ''} ${gen.LASTNAME_EN || ''}`.trim();

    const existing = await db.collection('volunteer_requests').findOne({ ptc_code: ptcCode });
    if (existing) {
      return res.status(400).json({ success: false, message: `Already submitted. Status: ${existing.status}` });
    }

    await db.collection('volunteer_requests').insertOne({
      ptc_code:     ptcCode,
      epic_no:      epicNo || gen.EPIC_NO || '',
      name,
      mobile:       gen.MOBILE_NO   || '',
      assembly:     gen.ASSEMBLY_NAME || '',
      district:     gen.DISTRICT_NAME || '',
      status:       'pending',
      requested_at: nowUTC(),
    });

    return res.json({ success: true, message: 'Volunteer request submitted!' });
  } catch (err) {
    console.error('request-volunteer error:', err);
    return res.status(500).json({ success: false, message: 'Server error' });
  }
});

// ────────────────────────────────────────────────────────────────
//  POST /request-booth-agent
// ────────────────────────────────────────────────────────────────
router.post('/request-booth-agent', async (req, res) => {
  try {
    const ptcCode = String(req.body.ptc_code || '').trim();
    const epicNo  = String(req.body.epic_no  || '').trim().toUpperCase();
    const boothNo = String(req.body.booth_no || '').trim();

    if (!ptcCode) return res.status(400).json({ success: false, message: 'PTC code required' });

    const db  = getDb();
    const gen = await db.collection('generated_voters').findOne({ ptc_code: ptcCode }) || {};
    const name = `${gen.FM_NAME_EN || ''} ${gen.LASTNAME_EN || ''}`.trim();

    const existing = await db.collection('booth_agent_requests').findOne({ ptc_code: ptcCode });
    if (existing) {
      return res.status(400).json({ success: false, message: `Already submitted. Status: ${existing.status}` });
    }

    await db.collection('booth_agent_requests').insertOne({
      ptc_code:     ptcCode,
      epic_no:      epicNo || gen.EPIC_NO || '',
      name,
      mobile:       gen.MOBILE_NO    || '',
      booth_no:     boothNo,
      assembly:     gen.ASSEMBLY_NAME || '',
      district:     gen.DISTRICT_NAME || '',
      status:       'pending',
      requested_at: nowUTC(),
    });

    return res.json({ success: true, message: 'Booth agent request submitted!' });
  } catch (err) {
    console.error('request-booth-agent error:', err);
    return res.status(500).json({ success: false, message: 'Server error' });
  }
});

module.exports = router;
