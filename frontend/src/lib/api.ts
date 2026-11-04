// Real ClaimGuard API — backed by the FastAPI service.
// Function signatures mirror the former mock layer so pages consume it unchanged.
import { api, downloadFile } from './apiClient';
import type {
  Claim,
  ClaimSummary,
  Provider,
  Member,
  QueueFilters,
  DashboardMetrics,
  SavingsDataPoint,
  USSDStats,
  TrustBadge,
} from '@/types';

// ─── Auth ───────────────────────────────────────────────────────────────────────

export interface AuthUser {
  id: string;
  email: string;
  fullName: string;
  isActive: boolean;
  isSuperuser: boolean;
  roles: string[];
  permissions: string[];
  /** Set for provider-portal accounts: the only provider they can see. */
  providerId?: string | null;
  lastLoginAt: string | null;
}

// ─── Provider portal (provider accounts only) ─────────────────────────────────

export interface PortalDashboard {
  provider: { code: string; name: string; type: string; city: string };
  trustScore: number;
  badge: TrustBadge;
  changeFromLastMonth: number | null;
  breakdown: {
    claimAccuracy: number;
    investigationSuccess: number | null;
    investigationsClosed: number;
    memberSatisfaction: number | null;
    shortfallIndex: number;
    flags90d: number;
    totalClaims: number;
    confidence: number;
  };
  peers: { region: string; count: number; average: number | null; percentile: number | null };
  history: { score: number; badge: string; reason: string | null; createdAt: string }[];
}

export interface PortalClaim {
  claimRef: string;
  submittedAt: string;
  serviceDate: string | null;
  amount: number;
  service: string | null;
  member: string;
  status: 'APPROVED' | 'UNDER_REVIEW' | 'DISPUTED' | 'REJECTED';
  disputeReason: string | null;
}

export interface PortalAppeal {
  id: string;
  status: 'OPEN' | 'UPHELD' | 'REJECTED';
  reason: string;
  scoreAtSubmission: number;
  outcomeNotes: string | null;
  submittedAt: string;
  resolvedAt: string | null;
}

export const fetchPortalDashboard = () => api.get<PortalDashboard>('/portal/provider/dashboard');
export const fetchPortalClaims = (days = 30) =>
  api.get<PortalClaim[]>('/portal/provider/claims', { params: { days } });
export const fetchPortalAppeals = () => api.get<PortalAppeal[]>('/portal/provider/appeals');
export const submitPortalAppeal = (reason: string) =>
  api.post<PortalAppeal>('/portal/provider/appeals', { reason });
export const downloadPortalReport = (days = 90) =>
  downloadFile('/portal/provider/report.csv', `claims-${days}d.csv`, { days });

export interface TokenPair {
  accessToken: string;
  refreshToken: string;
  tokenType: string;
  expiresIn: number;
}

export async function loginRequest(email: string, password: string): Promise<TokenPair> {
  return api.post<TokenPair>('/auth/login', { email, password });
}

export async function fetchMe(): Promise<AuthUser> {
  return api.get<AuthUser>('/auth/me');
}

export interface UserSummary {
  id: string;
  fullName: string;
  email: string;
  roles: string[];
  isActive: boolean;
}

export async function fetchUsers(includeInactive = false): Promise<UserSummary[]> {
  return api.get<UserSummary[]>('/users', {
    params: includeInactive ? { include_inactive: true } : undefined,
  });
}

export interface NewUserInput {
  fullName: string;
  email: string;
  password: string;
  roles: string[];
}

export async function createUser(input: NewUserInput): Promise<{ id: string; email: string }> {
  return api.post<{ id: string; email: string }>('/auth/users', input);
}

export interface UserUpdateInput {
  fullName?: string;
  roles?: string[];
  isActive?: boolean;
}

export async function updateUser(id: string, changes: UserUpdateInput): Promise<UserSummary> {
  return api.patch<UserSummary>(`/auth/users/${encodeURIComponent(id)}`, changes);
}

export async function deleteUser(id: string): Promise<void> {
  await api.del(`/auth/users/${encodeURIComponent(id)}`);
}

export async function logoutRequest(): Promise<void> {
  // Refresh token is read from the httpOnly cookie server-side.
  await api.post('/auth/logout', {});
}

// ─── Audit trail ────────────────────────────────────────────────────────────────

export interface AuditEntry {
  id: string;
  action: string;
  entityType: string;
  entityId: string | null;
  actorId: string | null;
  actorName: string | null;
  actorEmail: string | null;
  actorRoles: string[];
  requestId: string | null;
  ipAddress: string | null;
  changes: Record<string, unknown>;
  createdAt: string;
}

export interface AuditDetail {
  entry: AuditEntry;
  related: AuditEntry[];
}

export async function fetchAuditEntry(id: string): Promise<AuditDetail> {
  return api.get<AuditDetail>(`/audit/${encodeURIComponent(id)}`);
}

export interface PageInfo {
  page: number;
  pageSize: number;
  totalItems: number;
  totalPages: number;
  hasNext: boolean;
  hasPrev: boolean;
}

export interface AuditQuery {
  search?: string;
  action?: string;
  entityType?: string;
  page?: number;
  pageSize?: number;
}

export interface AuditResult {
  items: AuditEntry[];
  pagination: PageInfo;
}

export async function fetchAuditLog(q: AuditQuery = {}): Promise<AuditResult> {
  const env = await api.getEnvelope<AuditEntry[]>('/audit', {
    params: {
      page: q.page ?? 1,
      page_size: q.pageSize ?? 25,
      search: q.search || undefined,
      action: q.action || undefined,
      entity_type: q.entityType || undefined,
    },
  });
  const meta = env.metadata as { pagination?: PageInfo };
  const pagination = meta?.pagination ?? {
    page: 1, pageSize: 25, totalItems: env.data?.length ?? 0,
    totalPages: 1, hasNext: false, hasPrev: false,
  };
  return { items: env.data ?? [], pagination };
}

export interface AuditFilters {
  actions: string[];
  entityTypes: string[];
}

export async function fetchAuditFilters(): Promise<AuditFilters> {
  return api.get<AuditFilters>('/audit/filters');
}

// ─── Notifications ────────────────────────────────────────────────────────────

export interface NotificationItem {
  id: string;
  title: string;
  message: string;
  type: 'info' | 'warning' | 'alert';
  channel: string | null;
  link: string | null;
  read: boolean;
  createdAt: string;
}

export interface NotificationsResult {
  items: NotificationItem[];
  unread: number;
}

export async function fetchNotifications(): Promise<NotificationsResult> {
  const env = await api.getEnvelope<NotificationItem[]>('/notifications', {
    params: { page: 1, page_size: 50 },
  });
  const meta = env.metadata as { unread?: number };
  return { items: env.data ?? [], unread: meta?.unread ?? 0 };
}

export async function markNotificationRead(id: string): Promise<void> {
  await api.post(`/notifications/${encodeURIComponent(id)}/read`);
}

export async function markAllNotificationsRead(): Promise<void> {
  await api.post('/notifications/read-all');
}

export async function clearNotifications(): Promise<void> {
  await api.del('/notifications');
}

// ─── Investigations ───────────────────────────────────────────────────────────

export interface InvestigationComment {
  id: string;
  authorName: string | null;
  body: string;
  createdAt: string;
}

export interface Investigation {
  id: string;
  claimId: string;
  claimRef: string | null;
  decision: string | null;
  riskScore: number | null;
  memberName: string | null;
  providerName: string | null;
  claimedAmount: number | null;
  assignedTo: string | null;
  assignedToName: string | null;
  status: string;
  priority: string;
  resolution: string | null;
  resolutionNotes: string | null;
  resolvedAt: string | null;
  createdAt: string;
  comments: InvestigationComment[];
}

export interface InvestigationUpdate {
  status?: string;
  priority?: string;
  resolution?: string;
  resolutionNotes?: string;
  assignedTo?: string;
}

export async function fetchInvestigations(status?: string): Promise<Investigation[]> {
  return api.get<Investigation[]>('/investigations', {
    params: {
      page: 1,
      page_size: 200,
      status: status && status !== 'ALL' ? status : undefined,
    },
  });
}

export async function fetchInvestigation(id: string): Promise<Investigation | null> {
  return api.get<Investigation | null>(`/investigations/${encodeURIComponent(id)}`, {
    nullOn404: true,
  });
}

export async function openInvestigation(
  claimRef: string,
  priority?: string,
): Promise<Investigation> {
  return api.post<Investigation>('/investigations', { claimId: claimRef, priority });
}

export async function updateInvestigation(
  id: string,
  changes: InvestigationUpdate,
): Promise<Investigation> {
  return api.patch<Investigation>(`/investigations/${encodeURIComponent(id)}`, changes);
}

export async function addInvestigationComment(
  id: string,
  body: string,
): Promise<Investigation> {
  return api.post<Investigation>(
    `/investigations/${encodeURIComponent(id)}/comments`,
    { body },
  );
}

// ─── Dashboard ────────────────────────────────────────────────────────────────

export async function fetchDashboardMetrics(): Promise<DashboardMetrics> {
  return api.get<DashboardMetrics>('/dashboard/metrics');
}

export async function fetchSavingsData(): Promise<SavingsDataPoint[]> {
  return api.get<SavingsDataPoint[]>('/dashboard/savings');
}

export async function fetchUSSDStats(): Promise<USSDStats> {
  return api.get<USSDStats>('/dashboard/ussd');
}

// ─── Claims ─────────────────────────────────────────────────────────────────────

export async function fetchLiveFeed(): Promise<ClaimSummary[]> {
  return api.get<ClaimSummary[]>('/claims/live-feed', { params: { limit: 8 } });
}

export async function fetchQueue(filters?: Partial<QueueFilters>): Promise<ClaimSummary[]> {
  // Server-side filtering; a generous page size lets the client paginate the result set.
  return api.get<ClaimSummary[]>('/claims', {
    params: {
      page: 1,
      page_size: 200,
      search: filters?.search || undefined,
      priority: filters?.priority && filters.priority !== 'ALL' ? filters.priority : undefined,
      status: filters?.status && filters.status !== 'ALL' ? filters.status : undefined,
    },
  });
}

export async function fetchClaimByRef(claimRef: string): Promise<Claim | null> {
  return api.get<Claim | null>(`/claims/${encodeURIComponent(claimRef)}`, { nullOn404: true });
}

export async function approveClaim(claimRef: string): Promise<Claim> {
  return api.post<Claim>(`/claims/${encodeURIComponent(claimRef)}/approve`);
}

export async function rejectClaim(
  claimRef: string,
  reason: 'REJECT_FRAUD' | 'REJECT_ERROR',
): Promise<Claim> {
  return api.post<Claim>(`/claims/${encodeURIComponent(claimRef)}/reject`, { reason });
}

// ─── MemberGuard (instant alerts + confirmation) ─────────────────────────────────

export interface MemberAlert {
  channel: 'SMS' | 'WHATSAPP' | 'USSD';
  to: string;
  memberName: string;
  message: string;
  delivered: boolean;
}

export interface MemberAlertResult {
  claim: Claim;
  alert: MemberAlert;
}

export async function sendMemberAlert(
  claimRef: string,
  channel: 'SMS' | 'WHATSAPP' | 'USSD',
): Promise<MemberAlertResult> {
  return api.post<MemberAlertResult>(
    `/claims/${encodeURIComponent(claimRef)}/member-alert`,
    { channel },
  );
}

export async function recordMemberResponse(
  claimRef: string,
  response: 'CONFIRMED' | 'DISPUTED',
): Promise<Claim> {
  return api.post<Claim>(
    `/claims/${encodeURIComponent(claimRef)}/member-response`,
    { response },
  );
}

// ─── Providers / TrustScore ─────────────────────────────────────────────────────

export async function fetchProviders(): Promise<Provider[]> {
  return api.get<Provider[]>('/providers', { params: { page: 1, page_size: 200 } });
}

export async function fetchProviderByCode(code: string): Promise<Provider | null> {
  return api.get<Provider | null>(`/providers/${encodeURIComponent(code)}`, { nullOn404: true });
}

export async function fetchProviderClaims(providerCode: string): Promise<ClaimSummary[]> {
  return api.get<ClaimSummary[]>(`/providers/${encodeURIComponent(providerCode)}/claims`);
}

// ─── Members ──────────────────────────────────────────────────────────────────

export async function fetchMembers(): Promise<Member[]> {
  return api.get<Member[]>('/members', { params: { page: 1, page_size: 200 } });
}

export async function fetchMemberById(id: string): Promise<Member | null> {
  return api.get<Member | null>(`/members/${encodeURIComponent(id)}`, { nullOn404: true });
}

export async function fetchMemberClaims(memberId: string): Promise<Claim[]> {
  return api.get<Claim[]>(`/members/${encodeURIComponent(memberId)}/claims`);
}

// ─── Demo ───────────────────────────────────────────────────────────────────────

export async function runDemoScenario(scenarioId: string): Promise<void> {
  await api.post(`/demo/scenarios/${encodeURIComponent(scenarioId)}/run`);
}
