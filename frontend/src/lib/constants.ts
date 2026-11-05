import type { FlagCode, RiskLevel, ClaimDecision, TrustBadge, Priority, Channel } from '@/types';

// ─── Flag Labels ──────────────────────────────────────────────────────────────

export const FLAG_LABELS: Record<FlagCode, string> = {
  PRESCRIPTION_DATE_AFTER_SERVICE: 'Rx after service',
  HIGH_VALUE_NO_BIOMETRIC: 'No biometric',
  CHRONIC_DRUG_NO_CONDITION_REGISTERED: 'Unregistered condition',
  SHORTFALL_INFLATION_SUSPECTED: 'Shortfall inflated',
  STATISTICAL_ANOMALY_DETECTED: 'Statistical anomaly',
  POTENTIAL_FRAUD_SYNDICATE_DETECTED: 'Syndicate signal',
  MEMBER_DISPUTED: 'Member disputed',
  RECOVERY_REQUIRED: 'Recover payment',
  DUPLICATE_CLAIM: 'Duplicate',
  CLAIM_SPLITTING_SUSPECTED: 'Split claim',
  HIGH_CLAIM_FREQUENCY: 'Frequent claims',
  ITEMS_DO_NOT_MATCH_TOTAL: 'Items ≠ total',
};

export const FLAG_DESCRIPTIONS: Record<FlagCode, string> = {
  PRESCRIPTION_DATE_AFTER_SERVICE: 'Prescription was dated after the service date — indicating a possible post-dated or fabricated script.',
  HIGH_VALUE_NO_BIOMETRIC: 'Claim value exceeds threshold with no biometric confirmation recorded from the member.',
  CHRONIC_DRUG_NO_CONDITION_REGISTERED: 'Chronic medication dispensed but no matching condition is registered on the member\'s clinical profile.',
  SHORTFALL_INFLATION_SUSPECTED: 'Member shortfall significantly exceeds the expected range for this service type and provider.',
  STATISTICAL_ANOMALY_DETECTED: 'Claim metrics fall outside expected statistical norms for this provider and service combination.',
  POTENTIAL_FRAUD_SYNDICATE_DETECTED: 'Pattern matching detected — multiple members at this provider showing similar suspicious behavior.',
  MEMBER_DISPUTED: 'The member said they did not receive this service.',
  RECOVERY_REQUIRED: 'An investigation confirmed fraud on a claim that was already paid: the payment has to be recovered.',
  DUPLICATE_CLAIM: 'Same member, provider, service date and amount as an earlier claim: possible double billing.',
  CLAIM_SPLITTING_SUSPECTED: 'Several same-day claims at this provider each stay under the high-value line but together cross it.',
  HIGH_CLAIM_FREQUENCY: 'This member has an unusual number of claims in the past week.',
  ITEMS_DO_NOT_MATCH_TOTAL: 'The claimed amount differs from the sum of the claim\'s line items.',
};

// ─── Risk Level ───────────────────────────────────────────────────────────────

export const RISK_LEVEL_LABEL: Record<RiskLevel, string> = {
  LOW: 'Low Risk',
  MEDIUM: 'Moderate Risk',
  HIGH: 'High Risk',
  CRITICAL: 'Critical Risk',
};

export const RISK_LEVEL_COLOR: Record<RiskLevel, string> = {
  LOW: 'text-green-600',
  MEDIUM: 'text-amber-600',
  HIGH: 'text-orange-600',
  CRITICAL: 'text-red-600',
};

// ─── Decision ─────────────────────────────────────────────────────────────────

export const DECISION_LABEL: Record<ClaimDecision, string> = {
  APPROVE: 'Approved',
  PEND_VERIFY: 'Pending Verification',
  PEND_INVESTIGATE: 'Under Investigation',
  REJECT_FRAUD: 'Rejected — Fraud',
  REJECT_ERROR: 'Rejected — Error',
  MEMBER_DISPUTED: 'Member Disputed',
  ESCALATED: 'Escalated',
  INFO_NEEDED: 'Info Needed',
};

export const DECISION_COLOR: Record<ClaimDecision, string> = {
  APPROVE: 'bg-green-50 text-green-700 border-green-200',
  PEND_VERIFY: 'bg-amber-50 text-amber-700 border-amber-200',
  PEND_INVESTIGATE: 'bg-red-50 text-red-700 border-red-200',
  REJECT_FRAUD: 'bg-red-100 text-red-800 border-red-300',
  REJECT_ERROR: 'bg-orange-50 text-orange-700 border-orange-200',
  MEMBER_DISPUTED: 'bg-purple-50 text-purple-700 border-purple-200',
  ESCALATED: 'bg-gray-100 text-gray-700 border-gray-300',
  INFO_NEEDED: 'bg-sky-50 text-sky-700 border-sky-200',
};

// ─── Trust Badge ──────────────────────────────────────────────────────────────

export const TRUST_BADGE_COLOR: Record<TrustBadge, string> = {
  VERIFIED: 'bg-green-50 text-green-700 border-green-200',
  STANDARD: 'bg-blue-50 text-blue-700 border-blue-200',
  CAUTION: 'bg-amber-50 text-amber-700 border-amber-200',
  REVIEW: 'bg-red-50 text-red-700 border-red-200',
  WATCHLIST: 'bg-gray-100 text-gray-700 border-gray-300',
};

// ─── Priority ─────────────────────────────────────────────────────────────────

export const PRIORITY_COLOR: Record<Priority, string> = {
  CRITICAL: 'bg-red-600 text-white',
  HIGH: 'bg-orange-500 text-white',
  MEDIUM: 'bg-amber-500 text-white',
  LOW: 'bg-gray-400 text-white',
};

// ─── Channel ─────────────────────────────────────────────────────────────────

export const CHANNEL_COLOR: Record<Channel, string> = {
  WHATSAPP: 'bg-green-500 text-white',
  APP_FEED: 'bg-purple-500 text-white',
  USSD: 'bg-blue-500 text-white',
  SMS: 'bg-orange-500 text-white',
};

// ─── Detection Rates ─────────────────────────────────────────────────────────

export const DETECTION_RATES = {
  pessimistic: 0.04,
  conservative: 0.07,
  optimistic: 0.12,
} as const;
