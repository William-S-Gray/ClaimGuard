// ─── Risk & Decision ────────────────────────────────────────────────────────

export type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
export type ClaimDecision = 'APPROVE' | 'PEND_VERIFY' | 'PEND_INVESTIGATE' | 'REJECT_FRAUD' | 'REJECT_ERROR' | 'MEMBER_DISPUTED' | 'ESCALATED' | 'INFO_NEEDED';
export type ClaimStatus = ClaimDecision | 'SUBMITTED' | 'PROCESSING';

// ─── Flags ──────────────────────────────────────────────────────────────────

export type FlagCode =
  | 'PRESCRIPTION_DATE_AFTER_SERVICE'
  | 'HIGH_VALUE_NO_BIOMETRIC'
  | 'CHRONIC_DRUG_NO_CONDITION_REGISTERED'
  | 'SHORTFALL_INFLATION_SUSPECTED'
  | 'STATISTICAL_ANOMALY_DETECTED'
  | 'POTENTIAL_FRAUD_SYNDICATE_DETECTED';

// ─── Trust Score ─────────────────────────────────────────────────────────────

export type TrustBadge = 'VERIFIED' | 'STANDARD' | 'CAUTION' | 'REVIEW' | 'WATCHLIST';
export type ProviderType = 'PHARMACY' | 'GP' | 'SPECIALIST' | 'HOSPITAL' | 'LABORATORY';

// ─── Channel ─────────────────────────────────────────────────────────────────

export type Channel = 'WHATSAPP' | 'APP_FEED' | 'USSD' | 'SMS';
export type MemberResponseStatus = 'PENDING' | 'CONFIRMED' | 'DISPUTED' | 'NO_RESPONSE';

// ─── Priority ────────────────────────────────────────────────────────────────

export type Priority = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';

// ─── Member ──────────────────────────────────────────────────────────────────

export interface Member {
  id: string;
  memberNumber: string;
  name: string;
  plan: 'GOLD' | 'SILVER' | 'BRONZE';
  city: string;
  annualBenefit: number;
  benefitUsed: number;
  conditions: string[];
  phone: string;
  email: string;
  dateOfBirth: string;
}

// ─── Provider ────────────────────────────────────────────────────────────────

export interface Provider {
  id: string;
  code: string;
  name: string;
  type: ProviderType;
  city: string;
  trustScore: number;
  badge: TrustBadge;
  shortfallIndex: number;
  disputeRate: number;
  flags90d: number;
  totalClaims: number;
  averageClaimValue: number;
  phone: string;
  address: string;
  registrationDate: string;
  lastAuditDate: string;
}

// ─── Claim Items ─────────────────────────────────────────────────────────────

export interface ClaimItem {
  id: string;
  description: string;
  quantity: number;
  unitPrice: number;
  total: number;
  icd10Code?: string;
  nappiCode?: string;
}

// ─── Claim ────────────────────────────────────────────────────────────────────

export interface Claim {
  id: string;
  claimRef: string;
  nh263Ref: string;
  memberId: string;
  member: Member;
  providerId: string;
  provider: Provider;
  serviceDate: string;
  submittedAt: string;
  claimedAmount: number;
  approvedAmount?: number;
  memberShortfall: number;
  expectedShortfall: [number, number]; // [min, max]
  riskScore: number;
  riskLevel: RiskLevel;
  decision: ClaimDecision;
  flags: FlagCode[];
  items: ClaimItem[];
  aiExplanation: string;
  shapContributions: ShapContribution[];
  latencyMs: number;
  priority: Priority;
  autoApproveAt?: string; // ISO string, only for PEND_VERIFY
  memberNotificationSent: boolean;
  memberNotificationChannel?: Channel;
  memberResponse: MemberResponseStatus;
  memberRating?: number | null; // 1-5, after the member confirms
  agentNotes?: string;
  slaDeadline: string | null; // null while paused (Info needed)
  timeline: TimelineEvent[];
}

/** Member as list views get it: masked name ("T. M."), no contact or health details. */
export type MemberRef = Pick<Member, 'id' | 'name' | 'memberNumber' | 'plan'>;

/** A claim row from a list endpoint (queue, live feed, provider claims). */
export interface ClaimSummary extends Omit<Claim, 'member'> {
  member: MemberRef;
}

// ─── SHAP ─────────────────────────────────────────────────────────────────────

export interface ShapContribution {
  feature: string;
  contribution: number;
  direction: 'positive' | 'negative';
}

// ─── Timeline ─────────────────────────────────────────────────────────────────

export interface TimelineEvent {
  id: string;
  timestamp: string;
  event: string;
  description: string;
  actor: string;
  type: 'system' | 'agent' | 'member' | 'provider';
}

// ─── Dashboard ───────────────────────────────────────────────────────────────

export interface DashboardMetrics {
  claimsToday: number;
  claimsYesterday: number;
  flaggedToday: number;
  estimatedSaved: number;
  memberAlerts: number;
  memberRatingAvg: number | null;
  memberRatings: number;
  pendingInvestigation: number;
  autoApprovedToday: number;
  avgLatencyMs: number;
  detectionRate: number;
}

export interface SavingsDataPoint {
  month: string;
  savings: number;
  cumulative: number;
}

// ─── Queue ────────────────────────────────────────────────────────────────────

export type QueueSort = 'risk' | 'amount' | 'newest';

export interface QueueFilters {
  search: string;
  priority: Priority | 'ALL';
  status: ClaimStatus | 'ALL';
  riskLevel: RiskLevel | 'ALL';
  sort: QueueSort;
  /** Only claims with an investigation assigned to the signed-in user. */
  mine: boolean;
  dateFrom?: string;
  dateTo?: string;
}

// ─── USSD ─────────────────────────────────────────────────────────────────────

/** Member verification alerts sent over USSD, and how members replied. */
export interface USSDStats {
  alertsSent: number;
  confirmed: number;
  disputed: number;
  awaitingReply: number;
  /** The USSD code members dial; configured server-side (USSD_CODE). */
  code: string;
}

// ─── Demo ─────────────────────────────────────────────────────────────────────

export type ScenarioId = 'ghost-prescription' | 'shortfall-inflation' | 'clean-claim';

export interface DemoScenario {
  id: ScenarioId;
  name: string;
  description: string;
  memberName: string;
  providerName: string;
  amount: number;
  expectedRiskScore: number;
  expectedDecision: ClaimDecision;
  expectedLatency: number;
  flags: FlagCode[];
  color: 'red' | 'amber' | 'green';
}

export interface DemoStep {
  id: string;
  label: string;
  description: string;
  delayMs: number;
  completed: boolean;
  active: boolean;
}

// ─── ROI ──────────────────────────────────────────────────────────────────────

export interface ROIInputs {
  annualClaimsVolume: number;
  fraudErrorRate: number;
  detectionScenario: 'pessimistic' | 'conservative' | 'optimistic';
  modelImprovementRate: number;
  buildCost: number;
  annualMaintenance: number;
}

export interface ROIResults {
  fraudExposure: number;
  year1Savings: number;
  year1SystemCost: number;
  year1NetReturn: number;
  roiPercent: number;
  year3Projection: number;
  year5Projection: number;
  fiveYearCumulative: number;
}

// ─── WebSocket Events ────────────────────────────────────────────────────────

export type WSEventType =
  | 'claim_scored'
  | 'queue_updated'
  | 'member_response'
  | 'trustscore_updated'
  | 'whatsapp_delivered'
  | 'nh263_webhook';

export interface WSEvent {
  id: string;
  type: WSEventType;
  timestamp: string;
  payload: Record<string, unknown>;
}
