import { describe, expect, it } from 'vitest';
import { toolNeedsStructuredInput } from './toolSchema';

describe('toolNeedsStructuredInput', () => {
  it('is false for a tool with only flat string/number/boolean params', () => {
    expect(
      toolNeedsStructuredInput({
        inputSchema: { properties: { query: { type: 'string' }, limit: { type: 'number' } } },
      })
    ).toBe(false);
  });

  it('is true when any property is an object (e.g. an IAM policy document)', () => {
    expect(
      toolNeedsStructuredInput({
        inputSchema: { properties: { policy_document: { type: 'object' } } },
      })
    ).toBe(true);
  });

  it('is true when any property is an array', () => {
    expect(
      toolNeedsStructuredInput({
        inputSchema: { properties: { action_names: { type: 'array' } } },
      })
    ).toBe(true);
  });

  it('is true when only ONE of several properties is structured', () => {
    expect(
      toolNeedsStructuredInput({
        inputSchema: {
          properties: {
            role_name: { type: 'string' },
            trust_policy: { type: 'object' },
          },
        },
      })
    ).toBe(true);
  });

  it('is false for a tool with no inputSchema at all, or null/undefined', () => {
    expect(toolNeedsStructuredInput({})).toBe(false);
    expect(toolNeedsStructuredInput(null)).toBe(false);
    expect(toolNeedsStructuredInput(undefined)).toBe(false);
  });

  it('is false for an empty properties map', () => {
    expect(toolNeedsStructuredInput({ inputSchema: { properties: {} } })).toBe(false);
  });
});
