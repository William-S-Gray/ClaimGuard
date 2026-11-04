import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { QueueFilters } from '@/types';

interface UIState {
  sidebarOpen: boolean;
  queueFilters: QueueFilters;
  setSidebarOpen: (open: boolean) => void;
  toggleSidebar: () => void;
  setQueueFilters: (filters: Partial<QueueFilters>) => void;
  resetQueueFilters: () => void;
}

const DEFAULT_FILTERS: QueueFilters = {
  search: '',
  priority: 'ALL',
  status: 'ALL',
  riskLevel: 'ALL',
  sort: 'risk',
  mine: false,
};

// Queue filters survive a reload so investigators come back to the same view.
export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      sidebarOpen: true,
      queueFilters: DEFAULT_FILTERS,

      setSidebarOpen: (open) => set({ sidebarOpen: open }),
      toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),

      setQueueFilters: (filters) =>
        set((state) => ({ queueFilters: { ...state.queueFilters, ...filters } })),

      resetQueueFilters: () => set({ queueFilters: DEFAULT_FILTERS }),
    }),
    {
      name: 'claimguard-ui',
      partialize: (state) => ({ queueFilters: state.queueFilters }),
      // Filters saved before a field existed get that field's default.
      merge: (saved, current) => ({
        ...current,
        queueFilters: {
          ...DEFAULT_FILTERS,
          ...(saved as Partial<UIState> | undefined)?.queueFilters,
        },
      }),
    },
  ),
);
