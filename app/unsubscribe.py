"""Conservative RFC 8058 parsing, DKIM verification and pinned public HTTPS POST."""
import asyncio
import base64
import http.client
import ipaddress
import re
import socket
import ssl
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from urllib.parse import urlsplit, quote

from .services import UserError


def headers(raw):
    result = {}
    for h in raw.get('payload', {}).get('headers', []):
        result.setdefault(h['name'].lower(), []).append(h['value'])
    return result


def safe_url(value):
    try:
        if len(value) > 8192 or any(ord(c) <= 32 or ord(c) == 127 for c in value) or '\\' in value:
            return False
        u = urlsplit(value)
        if u.scheme != 'https' or not u.hostname or u.username is not None or u.password is not None or u.fragment or u.port not in (None, 443):
            return False
        host = u.hostname.encode('idna').decode('ascii').lower()
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            if '.' not in host or host.endswith(('.local', '.localhost', '.internal', '.test', '.invalid')) or host == 'localhost':
                return False
            return bool(re.fullmatch(r'[a-z0-9.-]+', host)) and not host.endswith('.')
    except (ValueError, UnicodeError):
        return False


def parse(raw):
    h = headers(raw)
    addresses = getaddresses(h.get('from', []))
    if len(addresses) != 1 or not re.fullmatch(r'[^\s<>@]+@[^\s<>@]+', addresses[0][1]):
        return None
    name, sender = addresses[0]
    sender = sender.casefold()
    lists = h.get('list-id', [])
    match = re.search(r'<([^<>\s]+)>', lists[0]) if len(lists) == 1 else None
    list_id = match[1].casefold() if match else None
    values = h.get('list-unsubscribe', [])
    links = re.findall(r'<([^<>]+)>', values[0]) if len(values) == 1 else []
    https = [link for link in links if safe_url(link)]
    mailto = [link for link in links if link.lower().startswith('mailto:') and not re.search(r'[\r\n]|%0[ad]', link, re.I)]
    evidence = bool(values or list_id)
    method = 'web' if https else 'mailto' if mailto else 'unknown'
    candidate = len(values) == 1 and len(https) == 1 and len([l for l in links if l.lower().startswith(('http:', 'https:'))]) == 1 and h.get('list-unsubscribe-post') == ['List-Unsubscribe=One-Click']
    return {'identity': 'list:' + list_id if list_id else 'sender:' + sender, 'list_id': list_id, 'sender': sender,
            'name': name or sender, 'evidence': evidence, 'method': method, 'url': https[0] if https else mailto[0] if mailto else None,
            'candidate': candidate, 'last_seen': int(raw.get('internalDate', 0)), 'reference': raw['id'], 'senders': [sender]}


def matching(raw, subscription, broader=False):
    record = parse(raw)
    if not record:
        return False
    if subscription['list_id']:
        return record['list_id'] == subscription['list_id']
    return record['sender'] in subscription['senders'] and (broader or (record['evidence'] and not record['list_id']))


def verify_dkim(encoded, expected):
    """Verify actual raw bytes; never trust email-supplied Authentication-Results."""
    try:
        import dkim
        import dns.resolver
        if len(encoded) > 34_000_000:
            return False
        content = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))
        if len(content) > 25_000_000:
            return False
        msg = BytesParser(policy=policy.default).parsebytes(content, headersonly=True)
        for name in ('List-Unsubscribe', 'List-Unsubscribe-Post', 'From'):
            if len(msg.get_all(name, [])) != 1:
                return False
        raw_record = parse({'id': expected['reference'], 'payload': {'headers': [{'name': k, 'value': str(v)} for k, v in msg.items()]}})
        if not raw_record or not raw_record['candidate'] or raw_record['url'] != expected['url'] or raw_record['identity'] != expected['identity'] or raw_record['sender'] != expected['sender']:
            return False
        resolver = dns.resolver.Resolver()
        resolver.lifetime = 5
        def dnsfunc(name, timeout=5):
            answers = resolver.resolve(name.decode('ascii').rstrip('.'), 'TXT', lifetime=min(timeout, 5))
            return b''.join(answers[0].strings)
        verifier = dkim.DKIM(content, timeout=5)
        for index, signature in enumerate(msg.get_all('DKIM-Signature', [])[:5]):
            tags = dict((k.strip().lower(), v.strip()) for k, v in re.findall(r'([a-zA-Z]+)\s*=\s*([^;]*)', str(signature)))
            signed = {v.strip().lower() for v in tags.get('h', '').split(':')}
            domain = tags.get('d', '').casefold()
            sender_domain = expected['sender'].rsplit('@', 1)[1]
            aligned = bool(domain and '.' in domain) and (domain == sender_domain or sender_domain.endswith('.' + domain))
            if aligned and {'from', 'list-unsubscribe', 'list-unsubscribe-post'} <= signed and verifier.verify(idx=index, dnsfunc=dnsfunc):
                return True
    except Exception:
        return False
    return False


async def capability(gmail, record):
    if not record['candidate']:
        return record['method']
    raw = await gmail.request('GET', 'messages/' + quote(record['reference'], safe=''), {'format': 'raw'})
    verified = await asyncio.to_thread(verify_dkim, raw.get('raw', ''), record)
    return 'one_click' if verified else record['method']


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=20, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Connect to the validated IP without a second DNS lookup; TLS still verifies hostname.
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def public_addresses(host):
    import dns.resolver
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 5
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        addresses = []
        for kind in ('A', 'AAAA'):
            try:
                addresses.extend(str(answer) for answer in resolver.resolve(host, kind, lifetime=5))
            except dns.resolver.NoAnswer:
                pass
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise UserError('Unsubscribe destination is not a public HTTPS server. Use a manual path.')
    return addresses


def post_one_click(url):
    if not safe_url(url):
        raise UserError('Unsafe or unsupported unsubscribe URL. Use a manual path.')
    connection = None
    try:
        parsed = urlsplit(url)
        host = parsed.hostname.encode('idna').decode('ascii')
        connection = PinnedHTTPS(host, public_addresses(host)[0])
        connection.request('POST', (parsed.path or '/') + ('?' + parsed.query if parsed.query else ''),
                           body=b'List-Unsubscribe=One-Click', headers={'Content-Type': 'application/x-www-form-urlencoded'})
        response = connection.getresponse()
        status = response.status
        if 300 <= status < 400:
            raise UserError('Unsubscribe endpoint redirected. No redirect was followed; complete unsubscribe manually.')
        if not 200 <= status < 300:
            raise UserError(f'Unsubscribe endpoint returned HTTP {status}. Completion is not verified.')
        return status
    except UserError:
        raise
    except Exception:
        raise UserError('Unsubscribe request failed or timed out. Completion is unknown; review before retrying.') from None
    finally:
        if connection:
            connection.close()
