import { cn } from '@/lib/utils';
import type { FlagCode } from '@/types';
import { FLAG_LABELS, FLAG_DESCRIPTIONS } from '@/lib/constants';
import { useState } from 'react';

interface FlagBadgeProps {
  flag: FlagCode;
  className?: string;
}

const FLAG_COLORS: Record<FlagCode, string> = {
  PRESCRIPTION_DATE_AFTER_SERVICE: 'bg-red-50 text-red-700 border-red-200',
  HIGH_VALUE_NO_BIOMETRIC: 'bg-orange-50 text-orange-700 border-orange-200',
  CHRONIC_DRUG_NO_CONDITION_REGISTERED: 'bg-red-50 text-red-700 border-red-200',
  SHORTFALL_INFLATION_SUSPECTED: 'bg-amber-50 text-amber-700 border-amber-200',
  STATISTICAL_ANOMALY_DETECTED: 'bg-purple-50 text-purple-700 border-purple-200',
  POTENTIAL_FRAUD_SYNDICATE_DETECTED: 'bg-red-100 text-red-800 border-red-300',
  MEMBER_DISPUTED: 'bg-red-100 text-red-800 border-red-300',
  RECOVERY_REQUIRED: 'bg-red-100 text-red-800 border-red-300',
  DUPLICATE_CLAIM: 'bg-red-50 text-red-700 border-red-200',
  CLAIM_SPLITTING_SUSPECTED: 'bg-orange-50 text-orange-700 border-orange-200',
  HIGH_CLAIM_FREQUENCY: 'bg-amber-50 text-amber-700 border-amber-200',
  ITEMS_DO_NOT_MATCH_TOTAL: 'bg-amber-50 text-amber-700 border-amber-200',
};

export function FlagBadge({ flag, className }: FlagBadgeProps) {
  const [showTooltip, setShowTooltip] = useState(false);

  return (
    <div className="relative inline-block">
      <button
        className={cn('badge cursor-help', FLAG_COLORS[flag], className)}
        onMouseEnter={() => setShowTooltip(true)}
        onMouseLeave={() => setShowTooltip(false)}
        onFocus={() => setShowTooltip(true)}
        onBlur={() => setShowTooltip(false)}
        aria-label={`Flag: ${FLAG_LABELS[flag]}. ${FLAG_DESCRIPTIONS[flag]}`}
        type="button"
      >
        ⚑ {FLAG_LABELS[flag]}
      </button>
      {showTooltip && (
        <div className="absolute bottom-full left-0 mb-2 z-50 w-64 p-3 bg-gray-900 text-white text-xs rounded-lg shadow-xl animate-fade-in">
          <p className="font-semibold mb-1">{FLAG_LABELS[flag]}</p>
          <p className="text-gray-300 leading-relaxed">{FLAG_DESCRIPTIONS[flag]}</p>
          <div className="absolute top-full left-4 -mt-px border-4 border-transparent border-t-gray-900" />
        </div>
      )}
    </div>
  );
}

interface FlagBadgeListProps {
  flags: FlagCode[];
  className?: string;
}

export function FlagBadgeList({ flags, className }: FlagBadgeListProps) {
  if (flags.length === 0) {
    return <span className="text-xs text-green-600 font-medium">✓ No flags</span>;
  }
  return (
    <div className={cn('flex flex-wrap gap-1', className)}>
      {flags.map((flag) => (
        <FlagBadge key={flag} flag={flag} />
      ))}
    </div>
  );
}
