"""请求签名：四川政府采购网（``www.ccgp-sichuan.gov.cn``）门户 JSON 接口所需。

为什么需要它
------------

该站首页是 Vue SPA，服务端不吐公告列表；真实数据出口是它自己的门户 JSON 接口
（``/gpcms/rest/web/v2/info/selectInfoMoreChannel``）。该接口**校验请求签名**，
无签名时返回 ``{"code":"4009","msg":"验签比对失败"}``，因此 ``http`` /
``search_api`` 两种策略都拿不到数据。

算法（2026-09-28 从**站点当前线上** bundle 逐字复核，非转述）
------------------------------------------------------------

站点版本 ``V6.5.17.0_5_20260729_gpcms-center-web``，``app.ec43fce8.js`` 模块 28590
（``SignEncryptUrl`` / ``SignEncryptMark``）与 ``chunk-vendors.83a9a133.js`` 模块 16295
（axios 请求拦截器用的 ``A(url, token)``）：

.. code-block:: javascript

    // 模块 16295 —— 实际设置请求头的那个函数
    p = e => d()( f().decode(e) )          // d = MD5, f = utf8（**不是 base64**）
    m = function (e, t) {                  // A(url, token)
      var n = (new Date).getTime();
      var i = p( h(`${n}_${String(e).split("?")[0]}_bosssoft_platform_095285`) );  // h = SHA1 → 十六进制串
      var r = {};
      r.time = n;
      r.url  = String(e).split("?")[0];
      r.sign = i;
      return r;
    }

    // 模块 28590 —— nsssjss（RSA 加密）
    Q = function (e) {
      var t = (new Date).getTime();
      return { timeStamp: t, nsssjss: K(String(e).split("?")[0] + "$$" + t) };
    }

即：

* ``sign = MD5( utf8(SHA1_hex(f"{ts}_{path}_{SALT}")) )``
* ``nsssjss = base64( RSA_PKCS1_v1_5( f"{path}$${ts2}" ) )``，公钥取自
  ``/gpcms-center-web/static/config.js`` 的 ``RSAPublicKey``
* 请求头 ``time`` / ``url`` / ``sign`` / ``nsssjss``；GET 另在 query 追加 ``_t=<ms>``
* ``path`` 一律取 **去掉 query 的路径**

⚠️ 与 ``/tmp/scraper_evidence/ccgp_sign.py`` 的差异（重要，别照抄旧结论）
----------------------------------------------------------------------

那份取证脚本认为「只有变体 C（``MD5(base64decode(SHA1_hex))``）能通」。**该结论现已失效**：

1. 模块 16295 里 ``f`` 是 **utf8** 模块（``chunk-vendors`` 模块 90031，``version="3.0.0"``，
   有 ``encode``/``decode`` 两个方法），**不是 base64 解码器**。对纯 ASCII 的 SHA1 十六进制串，
   ``utf8.decode`` 是恒等变换 → 实际算的是 ``MD5(SHA1_hex字符串)``，即变体 A。
2. 2026-09-28 现场实测（同一进程内 A/C 交替各 3 轮，间隔 3s）：
   ``variant A`` 3/3 返回 ``code=200 total=1561024``，``variant C`` 3/3 返回
   ``code=4009 验签比对失败``。可复现，非抖动。

因此本模块默认 ``sign_variant="sha1_hex_md5"``（= 变体 A，线上正确），
并把变体 C 作为 ``base64_sha1hex_md5`` 保留（站点若改回旧算法，改配置即可，无需改代码）。

实现约束
--------

* **纯 Python**：不调用 ``openssl`` 子进程，不引入 ``cryptography``。
  本模块自带的 ASN.1 DER 解析只覆盖 RSA SPKI 这一种结构，够用且可单测。
* **不硬编码站点数据**：SALT、公钥、路径、字段名全部来自 ``config/sources.yaml``。
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from functools import lru_cache
from typing import Callable

__all__ = [
    "SignError",
    "SIGN_VARIANTS",
    "decode_rsa_public_key",
    "parse_spki_public_key",
    "rsa_pkcs1v15_encrypt",
    "compute_mark",
    "build_signed_headers",
]

#: 支持的 ``sign`` 计算变体。线上实测只有 ``sha1_hex_md5`` 通过（见模块 docstring）。
SIGN_VARIANTS = ("sha1_hex_md5", "base64_sha1hex_md5", "sha1_raw_md5")

#: RSA 加密的 OID（1.2.840.113549.1.1.1 rsaEncryption）的 DER 编码
_RSA_OID = bytes.fromhex("2a864886f70d010101")

#: PKCS#1 v1.5 块类型 2 的填充开销：``0x00 0x02`` + PS(>=8) + ``0x00``
_PKCS1_MIN_PS = 8
_PKCS1_OVERHEAD = 3 + _PKCS1_MIN_PS


class SignError(Exception):
    """签名相关的配置/计算错误。**绝不静默降级成「不签名」**。"""


# ======================================================================
# 公钥：ASN.1 DER 解析（最小实现，只覆盖 RSA SPKI）
# ======================================================================
def decode_rsa_public_key(raw: str) -> str:
    """把配置里的公钥归一成 base64 的 SPKI DER 字符串。

    站点 ``config.js`` 里 ``RSAPublicKey`` 是**逗号分隔的 ASCII 码**（每个数字是
    一个字符的码点），拼起来才是一串 base64。手工把 392 个数字抄进 yaml 易错，
    所以两种形态都接受：

    * ``"77,73,71,102,..."`` → 逗号分隔 ASCII 码，按码点还原
    * ``"MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQ..."`` → 已经是 base64，原样返回
    """
    text = (raw or "").strip()
    if not text:
        raise SignError("RSA 公钥为空")
    if re.fullmatch(r"[\d,\s]+", text):
        parts = [p.strip() for p in text.split(",") if p.strip()]
        try:
            text = "".join(chr(int(p)) for p in parts)
        except ValueError as exc:  # 码点不是整数
            raise SignError(f"RSA 公钥 ASCII 码格式非法：{exc}") from exc
        text = text.strip()
    if not text:
        raise SignError("RSA 公钥解析后为空")
    return text


def _read_tlv(data: bytes, pos: int) -> tuple[int, bytes, int]:
    """读一个 DER TLV，返回 ``(tag, value, next_pos)``。"""
    if pos + 2 > len(data):
        raise SignError("DER 长度不足，公钥可能被截断")
    tag = data[pos]
    pos += 1
    first = data[pos]
    pos += 1
    if first & 0x80:
        n = first & 0x7F
        if n == 0 or n > 4:
            raise SignError(f"DER 长度域不支持：0x{first:02x}")
        if pos + n > len(data):
            raise SignError("DER 长度域超出数据范围")
        length = int.from_bytes(data[pos : pos + n], "big")
        pos += n
    else:
        length = first
    if pos + length > len(data):
        raise SignError("DER 内容超出数据范围")
    return tag, data[pos : pos + length], pos + length


def parse_spki_public_key(spki_der_b64: str) -> tuple[int, int]:
    """从 base64 的 SPKI DER 里取出 RSA 的 ``(n, e)``。

    只解析 ``SEQUENCE { AlgorithmIdentifier, BIT STRING { SEQUENCE { INTEGER n, INTEGER e } } }``
    这一种结构；其它结构显式报错，不做兜底猜测。
    """
    try:
        der = base64.b64decode(spki_der_b64, validate=True)
    except Exception as exc:  # binascii.Error 等
        raise SignError(f"公钥不是合法 base64：{exc}") from exc

    tag, outer, _ = _read_tlv(der, 0)
    if tag != 0x30:
        raise SignError(f"公钥顶层不是 SEQUENCE（tag=0x{tag:02x}）")

    tag, alg, pos = _read_tlv(outer, 0)
    if tag != 0x30:
        raise SignError("AlgorithmIdentifier 不是 SEQUENCE")
    tag, oid, _ = _read_tlv(alg, 0)
    if tag != 0x06 or oid != _RSA_OID:
        raise SignError("公钥算法不是 rsaEncryption，本模块只支持 RSA")

    tag, bit_string, _ = _read_tlv(outer, pos)
    if tag != 0x03:
        raise SignError("SubjectPublicKeyInfo 缺少 BIT STRING")
    if not bit_string or bit_string[0] != 0:
        raise SignError("BIT STRING 存在未用位，非标准 RSA 公钥")
    inner = bit_string[1:]

    tag, rsa_seq, _ = _read_tlv(inner, 0)
    if tag != 0x30:
        raise SignError("RSA 公钥体不是 SEQUENCE")
    tag, n_bytes, pos = _read_tlv(rsa_seq, 0)
    if tag != 0x02:
        raise SignError("RSA 模数不是 INTEGER")
    tag, e_bytes, _ = _read_tlv(rsa_seq, pos)
    if tag != 0x02:
        raise SignError("RSA 指数不是 INTEGER")

    n = int.from_bytes(n_bytes, "big")
    e = int.from_bytes(e_bytes, "big")
    if n <= 0 or e <= 1:
        raise SignError("RSA 模数/指数非法")
    return n, e


@lru_cache(maxsize=8)
def _cached_key(raw_public_key: str) -> tuple[int, int]:
    return parse_spki_public_key(decode_rsa_public_key(raw_public_key))


# ======================================================================
# RSA PKCS#1 v1.5 加密（纯 Python）
# ======================================================================
def rsa_pkcs1v15_encrypt(
    message: bytes,
    n: int,
    e: int,
    *,
    randbytes: Callable[[int], bytes] = os.urandom,
) -> bytes:
    """PKCS#1 v1.5 加密（EME-PKCS1-v1_5），返回定长密文。

    ``EM = 0x00 || 0x02 || PS || 0x00 || M``，``PS`` 为随机非零字节，
    ``|EM| = k``（模数字节长度）；``C = pow(EM, e, n)``，输出 k 字节。
    """
    k = (n.bit_length() + 7) // 8
    if k < 11:
        raise SignError(f"RSA 模数过短（{k} 字节），无法做 PKCS#1 v1.5 填充")
    if len(message) > k - _PKCS1_OVERHEAD:
        raise SignError(
            f"待加密内容 {len(message)} 字节超过 RSA 上限 {k - _PKCS1_OVERHEAD} 字节"
        )

    ps_len = k - 3 - len(message)
    ps = bytearray()
    while len(ps) < ps_len:
        chunk = randbytes(ps_len - len(ps))
        ps.extend(b for b in chunk if b != 0)
    em = b"\x00\x02" + bytes(ps[:ps_len]) + b"\x00" + message

    c = pow(int.from_bytes(em, "big"), e, n)
    return c.to_bytes(k, "big")


def rsa_encrypt_b64(
    message: str,
    raw_public_key: str,
    *,
    randbytes: Callable[[int], bytes] = os.urandom,
) -> str:
    """对 ``message`` 做 RSA PKCS#1 v1.5 加密并 base64 编码（等价 JSEncrypt.encrypt）。"""
    n, e = _cached_key(raw_public_key)
    return base64.b64encode(
        rsa_pkcs1v15_encrypt(message.encode("utf-8"), n, e, randbytes=randbytes)
    ).decode("ascii")


# ======================================================================
# 站点签名方案
# ======================================================================
def compute_mark(
    path: str,
    timestamp_ms: int,
    salt: str,
    *,
    variant: str = "sha1_hex_md5",
) -> str:
    """计算 ``sign``（= 站点前端 ``SignEncryptMark``）。

    ``variant`` 取值见 ``SIGN_VARIANTS``；默认 ``sha1_hex_md5`` 为线上实测通过的算法。
    """
    message = f"{timestamp_ms}_{path}_{salt}".encode("utf-8")
    sha1_hex = hashlib.sha1(message).hexdigest()
    if variant == "sha1_hex_md5":
        return hashlib.md5(sha1_hex.encode("utf-8")).hexdigest()
    if variant == "base64_sha1hex_md5":
        return hashlib.md5(base64.b64decode(sha1_hex)).hexdigest()
    if variant == "sha1_raw_md5":
        return hashlib.md5(hashlib.sha1(message).digest()).hexdigest()
    raise SignError(f"未知 sign 变体 {variant!r}，可选：{', '.join(SIGN_VARIANTS)}")


def build_signed_headers(
    path: str,
    *,
    salt: str,
    rsa_public_key: str,
    variant: str = "sha1_hex_md5",
    now_ms: int | None = None,
    randbytes: Callable[[int], bytes] = os.urandom,
) -> dict[str, str]:
    """构造该站门户接口所需的签名请求头。

    ``path`` 可含 query，本函数会自行截断（站点前端就是 ``String(url).split("?")[0]``）。
    """
    if variant not in SIGN_VARIANTS:
        raise SignError(f"未知 sign 变体 {variant!r}，可选：{', '.join(SIGN_VARIANTS)}")
    clean_path = str(path).split("?")[0]
    ts1 = int(now_ms if now_ms is not None else _now_ms())
    ts2 = int(now_ms if now_ms is not None else _now_ms())
    return {
        "time": str(ts1),
        "url": clean_path,
        "sign": compute_mark(clean_path, ts1, salt, variant=variant),
        "nsssjss": rsa_encrypt_b64(
            f"{clean_path}$${ts2}", rsa_public_key, randbytes=randbytes
        ),
    }


def _now_ms() -> int:
    import time as _time

    return int(_time.time() * 1000)
