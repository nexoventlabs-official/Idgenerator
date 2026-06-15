/**
 * SMS OTP service — uses 2factor.in API (same as Python app.py).
 */
const axios = require('axios');
const config = require('../config');

/**
 * Send OTP via 2factor.in API.
 * @param {string} mobile - 10-digit Indian mobile number
 * @param {string} otp - 6-digit OTP
 * @returns {{ success: boolean, message: string }}
 */
async function sendOtp(mobile, otp) {
  const apiKey = config.smsApiKey;

  if (!apiKey) {
    // Mock mode — log OTP to console in development
    console.log(`[SMS Mock] OTP for ${mobile}: ${otp}`);
    return { success: false, message: 'SMS API key not configured.' };
  }

  try {
    const url = `https://2factor.in/API/V1/${apiKey}/SMS/${mobile}/${otp}`;
    const resp = await axios.get(url, { timeout: 15000 });

    if (resp.status === 200 && resp.data && resp.data.Status === 'Success') {
      return { success: true, message: 'OTP sent successfully' };
    }

    console.warn('SMS API unexpected response:', resp.data);
    return { success: false, message: 'Could not send OTP. Please try again.' };
  } catch (err) {
    console.error('SMS send error:', err.message);
    return { success: false, message: 'Could not send OTP. Please try again.' };
  }
}

module.exports = { sendOtp };
