export async function request(url, options = {}) {
  const response = await fetch(url, options);
  const contentType = response.headers.get('content-type') || '';
  const data = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    throw new Error(readErrorMessage(data));
  }
  return data;
}

export function readErrorMessage(data) {
  if (typeof data === 'string') return data || '请求失败';
  const detail = data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors arrive as a list; new Error(list) renders as "[object Object]".
    const [first] = detail;
    const field = Array.isArray(first?.loc) ? first.loc.filter((part) => part !== 'body').join('.') : '';
    return `请求参数无效${field ? `（${field}）` : ''}：${first?.msg || '校验失败'}`;
  }
  return '请求失败';
}
