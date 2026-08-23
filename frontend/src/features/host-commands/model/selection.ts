import { create } from "zustand";

interface Selection {
  selected: string | null;
  select: (groupId: string) => void;
  clear: () => void;
}

/** The group the operator has picked, between duels. Client-only and
 * transient: it belongs to no frame, and it is read by two components that
 * are not parent and child. Nothing else belongs in this store — match
 * state has exactly one source, and it is the socket (H1). */
export const useSelection = create<Selection>((set) => ({
  selected: null,
  select: (groupId) => set({ selected: groupId }),
  clear: () => set({ selected: null }),
}));
