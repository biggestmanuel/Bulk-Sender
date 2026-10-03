import { describe, expect, it } from 'vitest'
import { classifyPhone, getCountryList, isValidPhone, resolveWithCountry } from './phone'

describe('classifyPhone', () => {
  it('accepts a valid international number', () => {
    const result = classifyPhone('+2348161234765')
    expect(result.status).toBe('valid')
    expect(result.e164).toBe('+2348161234765')
  })

  it('rejects an empty value', () => {
    expect(classifyPhone('').status).toBe('invalid')
    expect(classifyPhone('   ').status).toBe('invalid')
    expect(classifyPhone(null).status).toBe('invalid')
    expect(classifyPhone(undefined).status).toBe('invalid')
  })

  /**
   * A number with no + could belong to any country, and guessing would send
   * the message to the wrong person. These get a distinct status so the UI
   * can ask instead of silently rejecting.
   */
  it('flags a number with no country code', () => {
    expect(classifyPhone('08012345678').status).toBe('needs-country')
    expect(classifyPhone('447911123456').status).toBe('needs-country')
  })

  it('rejects an impossible number for its country code', () => {
    expect(classifyPhone('+2341').status).toBe('invalid')
    expect(classifyPhone('+1234567890123').status).toBe('invalid')
  })

  it('rejects letters and symbols', () => {
    expect(classifyPhone('+234abc1234').status).toBe('invalid')
    expect(classifyPhone('***').status).toBe('invalid')
  })

  it('ignores surrounding whitespace', () => {
    expect(classifyPhone('  +2348161234765  ').status).toBe('valid')
  })

  it('normalizes to E.164', () => {
    // NBSP and zero-width space are common in spreadsheet exports.
    expect(classifyPhone('+234 816\u00a0123 4765').e164).toBe('+2348161234765')
  })
})

describe('resolveWithCountry', () => {
  it('combines a country code with national digits', () => {
    const result = resolveWithCountry('08012345678', 'NG')
    expect(result.status).toBe('valid')
    expect(result.e164).toMatch(/^\+234/)
  })

  it('works for a US number missing its country code', () => {
    const result = resolveWithCountry('4155552671', 'US')
    expect(result.status).toBe('valid')
    expect(result.e164).toBe('+14155552671')
  })

  it('rejects digits that are wrong for the chosen country', () => {
    expect(resolveWithCountry('12345', 'NG').status).toBe('invalid')
    expect(resolveWithCountry('999999999999999', 'NG').status).toBe('invalid')
  })

  it('rejects empty input', () => {
    expect(resolveWithCountry('', 'NG').status).toBe('invalid')
    expect(resolveWithCountry('abc', 'NG').status).toBe('invalid')
  })
})

describe('isValidPhone', () => {
  it('is the boolean form of classifyPhone', () => {
    expect(isValidPhone('+2348161234765')).toBe(true)
    expect(isValidPhone('08012345678')).toBe(false)
    expect(isValidPhone('')).toBe(false)
  })
})

describe('getCountryList', () => {
  it('returns countries sorted by name with calling codes', () => {
    const countries = getCountryList()

    expect(countries.length).toBeGreaterThan(100)
    expect(countries[0]).toHaveProperty('code')
    expect(countries[0]).toHaveProperty('name')
    expect(countries[0]).toHaveProperty('callingCode')

    const names = countries.map((c) => c.name)
    expect([...names].sort((a, b) => a.localeCompare(b))).toEqual(names)
  })

  it('is cached between calls', () => {
    expect(getCountryList()).toBe(getCountryList())
  })

  it('includes the countries the picker is actually used for', () => {
    const codes = new Set(getCountryList().map((c) => c.code))
    for (const code of ['NG', 'GH', 'KE', 'US', 'GB', 'ZA']) {
      expect(codes.has(code)).toBe(true)
    }
  })
})