import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchUSSDStats } from '@/lib/api';
import { AppShell } from '@/components/layout/AppShell';
import { StatCard } from '@/components/shared/StatCard';
import { cn } from '@/lib/utils';
import { Hash, CheckCircle, XCircle, HelpCircle, Phone, MessageSquare, AlertCircle } from 'lucide-react';

type ScreenKey =
  | 'home'
  | 'pending'
  | 'recent'
  | 'balance'
  | 'report'
  | 'call'
  | 'pending_detail'
  | 'confirm'
  | 'dispute'
  | 'dispute_done';

interface Screen {
  title: string;
  body: string[];
  options?: { key: string; label: string; next: ScreenKey }[];
  isDone?: boolean;
}

const SCREENS: Record<ScreenKey, Screen> = {
  home: {
    title: 'CIMAS CLAIMGUARD 360°\n*483*360#',
    body: [
      'Hi Tendai!',
      'ClaimGuard is protecting',
      'your benefits.',
      '',
      '1 claim needs your',
      'confirmation.',
    ],
    options: [
      { key: '1', label: 'Check pending claims', next: 'pending' },
      { key: '2', label: 'View recent claims', next: 'recent' },
      { key: '3', label: 'Check balance', next: 'balance' },
      { key: '4', label: 'Report problem', next: 'report' },
      { key: '5', label: 'Call Cimas', next: 'call' },
    ],
  },
  pending: {
    title: 'PENDING CLAIMS',
    body: [
      'CG-00291 · 30 Jun 2026',
      'City Pharmacy Bulawayo',
      'Amount: $88.00',
      'Shortfall: $22.00',
      '4 items dispensed',
      '',
      'Did you visit today?',
    ],
    options: [
      { key: '1', label: 'Yes, I visited (Confirm)', next: 'confirm' },
      { key: '2', label: 'No / Something is wrong', next: 'dispute' },
      { key: '0', label: 'Back', next: 'home' },
    ],
  },
  pending_detail: {
    title: 'CLAIM ITEMS',
    body: [
      '1. Insulin Human 100U/mL ×2',
      '   $38.00',
      '2. Metformin 500mg ×1',
      '   $12.00',
      '3. Glibenclamide 5mg ×1',
      '   $18.00',
      '4. Lisinopril 10mg ×1',
      '   $20.00',
      '',
      'Total: $88.00',
    ],
    options: [
      { key: '1', label: 'Confirm this claim', next: 'confirm' },
      { key: '2', label: 'Dispute this claim', next: 'dispute' },
      { key: '0', label: 'Back', next: 'pending' },
    ],
  },
  recent: {
    title: 'RECENT CLAIMS',
    body: [
      'CG-00882 · 30 Jun',
      'Avenues Pharmacy · APPROVED',
      '$22.00',
      '',
      'CG-00088 · 28 Jun',
      'QuickCare Pharmacy · APPROVED',
      '$35.00',
    ],
    options: [{ key: '0', label: 'Back to home', next: 'home' }],
  },
  balance: {
    title: 'BENEFIT BALANCE',
    body: [
      'Member: Tendai Moyo',
      'Plan: GOLD',
      '',
      'Annual Benefit: $2,500.00',
      'Used to date: $652.50',
      'Remaining: $1,847.50',
      '',
      '26% of benefit used',
    ],
    options: [{ key: '0', label: 'Back', next: 'home' }],
  },
  report: {
    title: 'REPORT A PROBLEM',
    body: [
      'To report a problem:',
      '',
      'Call: 0800 CIMAS 1',
      'WhatsApp: +263 71 900 0001',
      'Email: guard@cimas.co.zw',
      '',
      'Our team is available',
      'Mon–Fri 8am–5pm',
    ],
    options: [{ key: '0', label: 'Back', next: 'home' }],
  },
  call: {
    title: 'CALL CIMAS',
    body: [
      'Cimas Member Services',
      '',
      'Toll-free: 0800 24 CIMAS',
      '+263 24 2777 000',
      '',
      'Hours: 24/7 for emergencies',
      'Mon–Fri 8am–5pm for admin',
    ],
    options: [{ key: '0', label: 'Back', next: 'home' }],
  },
  confirm: {
    title: '✓ CLAIM CONFIRMED',
    body: [
      'Thank you Tendai!',
      '',
      'Claim CG-00291 has been',
      'confirmed by you.',
      '',
      'Cimas will process your',
      'claim within 2 business',
      'days.',
      '',
      'Ref: CG-00291',
    ],
    isDone: true,
    options: [{ key: '0', label: 'Back to home', next: 'home' }],
  },
  dispute: {
    title: '✗ CLAIM DISPUTED',
    body: [
      'Thank you for reporting.',
      '',
      'We have flagged claim',
      'CG-00291 for investigation.',
      '',
      'A Cimas fraud agent will',
      'contact you within',
      '24 hours.',
      '',
      'Case ref: DISP-2026-00291',
    ],
    isDone: true,
    options: [{ key: '0', label: 'Back to home', next: 'home' }],
  },
  dispute_done: {
    title: 'CASE FILED',
    body: ['Your case has been filed.'],
    options: [{ key: '0', label: 'Home', next: 'home' }],
  },
};

export function USSDSimulatorPage() {
  const [currentScreen, setCurrentScreen] = useState<ScreenKey>('home');
  const [history, setHistory] = useState<ScreenKey[]>([]);
  const { data: stats } = useQuery({ queryKey: ['ussd-stats'], queryFn: fetchUSSDStats });

  const screen = SCREENS[currentScreen];

  const navigate = (next: ScreenKey) => {
    setHistory((h) => [...h, currentScreen]);
    setCurrentScreen(next);
  };

  const goBack = () => {
    if (history.length > 0) {
      const prev = history[history.length - 1];
      setHistory((h) => h.slice(0, -1));
      setCurrentScreen(prev);
    }
  };

  const reset = () => {
    setCurrentScreen('home');
    setHistory([]);
  };

  return (
    <AppShell title="USSD Simulator" subtitle="*483*360# · Econet · NetOne · Telecel">
      <div className="max-w-screen-xl mx-auto space-y-5">

        {/* Carrier + stats */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <StatCard title="Alerts Sent" value={stats?.alertsSent ?? '—'} subtitle="Over USSD" icon={<Phone size={18} />} accentColor="#1A4D8F" />
          <StatCard title="Confirmations" value={stats?.confirmed ?? '—'} subtitle="Members confirmed" icon={<CheckCircle size={18} />} accentColor="#16A34A" />
          <StatCard title="Disputes Filed" value={stats?.disputed ?? '—'} subtitle="Fraud flags raised" icon={<XCircle size={18} />} accentColor="#DC2626" />
          <StatCard title="Awaiting Reply" value={stats?.awaitingReply ?? '—'} subtitle="No answer yet" icon={<MessageSquare size={18} />} accentColor="#7B2D8B" />
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
          {/* Phone simulator */}
          <div className="xl:col-span-1 flex justify-center">
            <div className="w-72">
              {/* Feature phone frame */}
              <div className="bg-gray-800 rounded-[2.5rem] p-4 shadow-2xl">
                <div className="bg-gray-700 rounded-[2rem] overflow-hidden">
                  {/* Screen */}
                  <div className="bg-[#B5D5A0] p-4 min-h-64 font-mono text-xs text-gray-900">
                    <div className="whitespace-pre-line font-bold text-[10px] text-gray-700 mb-1 border-b border-gray-400 pb-1">
                      {screen.title}
                    </div>
                    <div className="mt-2 space-y-0.5 text-[11px] leading-relaxed">
                      {screen.body.map((line, i) => (
                        <div key={i} className={line === '' ? 'h-2' : ''}>{line || ''}</div>
                      ))}
                    </div>
                    {screen.options && (
                      <div className="mt-3 border-t border-gray-400 pt-2 space-y-0.5 text-[10px]">
                        {screen.options.map((opt) => (
                          <div key={opt.key}>{opt.key}. {opt.label}</div>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Keypad */}
                  <div className="bg-gray-600 p-3">
                    <div className="grid grid-cols-3 gap-1.5 mb-1.5">
                      {['1', '2', '3', '4', '5', '6', '7', '8', '9', '*', '0', '#'].map((key) => {
                        const option = screen.options?.find((o) => o.key === key);
                        const isActive = !!option;
                        return (
                          <button
                            key={key}
                            onClick={() => option && navigate(option.next)}
                            disabled={!isActive}
                            className={cn(
                              'py-2 rounded-lg text-xs font-bold transition-colors',
                              isActive
                                ? 'bg-gray-800 text-white hover:bg-gray-700 active:bg-black'
                                : 'bg-gray-700 text-gray-600 cursor-not-allowed'
                            )}
                            aria-label={`Key ${key}${option ? ': ' + option.label : ''}`}
                          >
                            {key}
                          </button>
                        );
                      })}
                    </div>
                    <div className="grid grid-cols-3 gap-1.5">
                      <button
                        onClick={goBack}
                        disabled={history.length === 0}
                        className={cn('py-1.5 rounded-lg text-[10px] font-bold transition-colors',
                          history.length > 0 ? 'bg-amber-600 text-white hover:bg-amber-700' : 'bg-gray-700 text-gray-600 cursor-not-allowed'
                        )}
                        aria-label="Go back"
                      >
                        BACK
                      </button>
                      <button
                        onClick={reset}
                        className="py-1.5 rounded-lg text-[10px] font-bold bg-red-600 text-white hover:bg-red-700 transition-colors"
                        aria-label="End session"
                      >
                        END
                      </button>
                      <button
                        onClick={() => navigate('home')}
                        className="py-1.5 rounded-lg text-[10px] font-bold bg-green-600 text-white hover:bg-green-700 transition-colors"
                        aria-label="Go to home screen"
                      >
                        HOME
                      </button>
                    </div>
                  </div>
                </div>
              </div>
              <p className="text-center text-xs text-gray-400 mt-3">Click keypad numbers to navigate</p>
            </div>
          </div>

          {/* Stats panel */}
          <div className="xl:col-span-2 space-y-4">
            {/* Session outcomes */}
            <div className="page-card p-5">
              <h3 className="mb-4">Member Replies</h3>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                {[
                  { label: 'Claims confirmed', value: stats?.confirmed ?? '—', icon: <CheckCircle size={20} className="text-green-500" />, bg: 'bg-green-50' },
                  { label: 'Disputes raised', value: stats?.disputed ?? '—', icon: <AlertCircle size={20} className="text-red-500" />, bg: 'bg-red-50' },
                  { label: 'Awaiting reply', value: stats?.awaitingReply ?? '—', icon: <HelpCircle size={20} className="text-purple-500" />, bg: 'bg-purple-50' },
                ].map((item) => (
                  <div key={item.label} className={cn('rounded-xl p-4 text-center', item.bg)}>
                    <div className="flex justify-center mb-2">{item.icon}</div>
                    <p className="text-2xl font-bold text-gray-900">{item.value}</p>
                    <p className="text-xs text-gray-500 mt-0.5">{item.label}</p>
                  </div>
                ))}
              </div>
            </div>

            {/* Navigation history */}
            {history.length > 0 && (
              <div className="page-card p-5">
                <h3 className="mb-3">Current Session Path</h3>
                <div className="flex items-center gap-1.5 flex-wrap">
                  {[...history, currentScreen].map((key, i) => (
                    <span key={i} className="flex items-center gap-1.5">
                      {i > 0 && <span className="text-gray-300">›</span>}
                      <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full capitalize">
                        {key.replace(/_/g, ' ')}
                      </span>
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </AppShell>
  );
}
