import { describe, expect, it } from 'vitest';
import { getServiceIcon } from './serviceIconRegistry';

describe('getServiceIcon', () => {
  it('returns a known service icon spec by key', () => {
    const spec = getServiceIcon('slack');
    expect(spec.color).toBe('#4A154B');
    expect(spec.Icon).toBeDefined();
  });

  it('falls back to the default icon for an unknown key', () => {
    const spec = getServiceIcon('some_connector_that_does_not_exist');
    expect(spec.color).toBe('#8A94A6');
  });

  it('falls back to the default icon for null/undefined/empty', () => {
    expect(getServiceIcon(null).color).toBe('#8A94A6');
    expect(getServiceIcon(undefined).color).toBe('#8A94A6');
    expect(getServiceIcon('').color).toBe('#8A94A6');
  });

  it('gives the built-in database/vector engines their real brand colors, not a generic fallback', () => {
    expect(getServiceIcon('sqlite').color).toBe('#003B57');
    expect(getServiceIcon('qdrant').color).toBe('#DC244C');
  });
});
