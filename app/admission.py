"""Shared execution admission. Redis accelerates limits; PostgreSQL fences claims.

No user content, credentials or model configuration enters coordination keys.
Admission limits Run starts, not individual model requests or token/cost usage.
"""
from dataclasses import dataclass
import hashlib
import json
import uuid


class CoordinationUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class AdmissionPolicy:
    namespace: str
    capacity: int
    starts_per_minute: int

    def __post_init__(self):
        if str(uuid.UUID(self.namespace)) != self.namespace:
            raise ValueError('Admission namespace must be a canonical UUID')
        if type(self.capacity) is not int or not 1 <= self.capacity <= 1000:
            raise ValueError('Admission capacity must be 1–1000')
        if type(self.starts_per_minute) is not int or not 1 <= self.starts_per_minute <= 60000:
            raise ValueError('Admission start rate must be 1–60000')

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps([self.namespace,self.capacity,self.starts_per_minute],
            separators=(',',':')).encode()).hexdigest()


# All keys share a Redis Cluster hash tag. TIME is server time; an expired
# permit can never resurrect. policy mismatch is not treated as free capacity.
SCRIPT = """
local policy = redis.call('GET', KEYS[1])
if policy and policy ~= ARGV[1] then return -1 end
local clock = redis.call('TIME')
local now = tonumber(clock[1])*1000 + math.floor(tonumber(clock[2])/1000)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
redis.call('ZREMRANGEBYSCORE', KEYS[3], '-inf', now-60000)
local action, owner = ARGV[2], ARGV[3]
if action == 'acquire' then
  if redis.call('ZSCORE',KEYS[2],owner) then return -1 end
  if redis.call('ZCARD',KEYS[2]) >= tonumber(ARGV[4]) then return 0 end
  if redis.call('ZCARD',KEYS[3]) >= tonumber(ARGV[5]) then return 0 end
  redis.call('SET',KEYS[1],ARGV[1])
  redis.call('ZADD',KEYS[2],now+30000,owner)
  redis.call('ZADD',KEYS[3],now,owner)
  redis.call('PEXPIRE',KEYS[2],90000)
  redis.call('PEXPIRE',KEYS[3],120000)
  return 1
elseif action == 'renew' then
  if not policy or not redis.call('ZSCORE',KEYS[2],owner) then return -1 end
  redis.call('ZADD',KEYS[2],now+30000,owner)
  redis.call('PEXPIRE',KEYS[2],90000)
  return 1
elseif action == 'release' then
  redis.call('ZREM',KEYS[2],owner)
  if ARGV[6] == 'refund' then redis.call('ZREM',KEYS[3],owner) end
  return 1
end
return -1
"""


class RedisAdmission:
    def __init__(self, client, policy):
        self.client,self.policy = client,policy
        prefix = 'cs:admission:{'+policy.namespace+'}:'
        self.keys = [prefix+'policy',prefix+'slots',prefix+'starts']

    @classmethod
    def from_url(cls, url, policy):
        from redis import Redis
        from redis.backoff import NoBackoff
        from redis.retry import Retry
        import ipaddress
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        if parsed.scheme not in {'redis','rediss'} or not parsed.hostname or parsed.query or parsed.fragment:
            raise ValueError('Use an explicit Redis URL without query overrides')
        if parsed.scheme == 'redis':
            try:
                loopback = ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                loopback = parsed.hostname == 'localhost'
            if not loopback:
                raise ValueError('Remote Redis requires TLS')
        # No SDK retries: uncertain acquire fails closed; its orphan expires.
        return cls(Redis.from_url(url,socket_connect_timeout=1,socket_timeout=1,
            retry_on_timeout=False,retry=Retry(NoBackoff(),0),max_connections=8,decode_responses=True),policy)

    def _call(self, action, owner, refund=False):
        if str(uuid.UUID(owner)) != owner:
            raise ValueError('Invalid coordination permit')
        try:
            result = self.client.eval(SCRIPT,len(self.keys),*self.keys,self.policy.fingerprint,
                action,owner,self.policy.capacity,self.policy.starts_per_minute,'refund' if refund else '')
        except Exception:
            raise CoordinationUnavailable('Shared admission is unavailable') from None
        if type(result) is not int or result not in (-1,0,1) or result == -1:
            raise CoordinationUnavailable('Shared admission policy or permit is unavailable')
        return result == 1

    def acquire(self):
        owner = str(uuid.uuid4())
        return owner if self._call('acquire',owner) else None

    def require(self, owner):
        if not self._call('renew',owner):
            raise CoordinationUnavailable('Shared admission permit was lost')

    def release(self, owner, *, refund=False):
        self._call('release',owner,refund)

    def close(self):
        self.client.close()
