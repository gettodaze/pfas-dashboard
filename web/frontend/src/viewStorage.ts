// View preferences stay in this browser and never include selected jobs or inputs.
export function readView<T extends object>(key: string): Partial<T> {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "null");
    return value && typeof value === "object" && !Array.isArray(value)
      ? value
      : {};
  } catch {
    return {};
  }
}

export function saveView(key: string, value: object) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Browsing still works when storage is disabled or full.
  }
}
