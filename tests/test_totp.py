"""El TOTP (api/totp.py) probado contra los vectores OFICIALES de la RFC 6238 y contra sus reglas de seguridad (formato, ventana, no repetir)."""

import base64

import pytest

from modelo_ia.api import totp

# Apéndice B de la RFC 6238: secreto ASCII «12345678901234567890», SHA-1, 8 dígitos
SECRETO_RFC = base64.b32encode(b"12345678901234567890").decode()
VECTORES = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]
T0 = 1_700_000_010  # un instante cualquiera: está a 10 s del inicio de su paso de 30 s
PASO0 = T0 // 30


@pytest.mark.parametrize("instante,esperado", VECTORES)
def test_los_codigos_coinciden_con_los_vectores_oficiales_de_la_rfc_6238(instante, esperado):
    assert totp.codigo(SECRETO_RFC, instante, 8) == esperado
    assert totp.codigo(SECRETO_RFC, instante) == esperado[-6:]  # a 6 dígitos son los últimos 6 del de 8


def test_el_vector_de_la_rfc_4226_para_hotp_con_los_primeros_contadores():
    clave = b"12345678901234567890"
    esperados = [
        "755224",
        "287082",
        "359152",
        "969429",
        "338314",
        "254676",
        "287922",
        "162583",
        "399871",
        "520489",
    ]  # Apéndice D de la RFC 4226
    assert [totp._hotp(clave, n) for n in range(10)] == esperados


def test_un_codigo_valido_devuelve_su_paso_y_el_secreto_puede_llevar_espacios_y_minusculas():
    c = totp.codigo(SECRETO_RFC, T0)
    assert totp.verificar(SECRETO_RFC, c, T0) == PASO0
    assert totp.verificar(SECRETO_RFC.lower(), c, T0) == PASO0
    assert totp.verificar(" ".join(SECRETO_RFC[i : i + 4] for i in range(0, len(SECRETO_RFC), 4)), c, T0) == PASO0


def test_se_acepta_un_paso_antes_y_uno_despues_pero_no_dos():
    for desfase, aceptado in ((-2, False), (-1, True), (0, True), (1, True), (2, False)):
        c = totp.codigo(SECRETO_RFC, T0 + desfase * 30)
        assert (totp.verificar(SECRETO_RFC, c, T0) is not None) is aceptado, desfase


def test_un_codigo_no_se_puede_repetir_ni_uno_mas_viejo_que_el_ultimo_aceptado():
    actual, anterior, siguiente = (totp.codigo(SECRETO_RFC, T0 + d * 30) for d in (0, -1, 1))
    assert totp.verificar(SECRETO_RFC, actual, T0, ultimo_paso=PASO0 - 1) == PASO0
    assert totp.verificar(SECRETO_RFC, actual, T0, ultimo_paso=PASO0) is None  # el mismo paso ya se usó
    assert totp.verificar(SECRETO_RFC, anterior, T0, ultimo_paso=PASO0) is None  # y uno más viejo tampoco
    assert totp.verificar(SECRETO_RFC, siguiente, T0, ultimo_paso=PASO0) == PASO0 + 1  # uno más nuevo sí


@pytest.mark.parametrize(
    "raro",
    [
        "",
        "12345",
        "1234567",
        "12345a",
        "١٢٣٤٥٦",
        "１２３４５６",
        " 123456",
        "123456 ",
        "123456\n",
        "12 456",
        "+12345",
        "-12345",
        None,
        123456,
        b"123456",
        ["1"],
        1.5,
    ],
)
def test_cualquier_cosa_que_no_sean_6_digitos_ascii_se_rechaza_sin_romperse(raro):
    assert totp.verificar(SECRETO_RFC, raro, T0) is None


def test_un_codigo_con_digitos_de_otro_alfabeto_no_cuenta_aunque_str_isdigit_diga_que_si():
    arabe = "".join(chr(0x0660 + int(d)) for d in totp.codigo(SECRETO_RFC, T0))
    assert arabe.isdigit() and not arabe.isascii()
    assert totp.verificar(SECRETO_RFC, arabe, T0) is None


def test_los_secretos_nuevos_tienen_160_bits_en_base32_y_no_se_repiten():
    secretos = {totp.nuevo_secreto() for _ in range(200)}
    assert len(secretos) == 200
    for s in secretos:
        assert len(s) == 32 and set(s) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567") and len(base64.b32decode(s)) == 20


def test_el_enlace_otpauth_lleva_lo_que_necesita_la_app_y_escapa_los_nombres():
    u = totp.uri("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567", "Marlon A. (admin)", "Antifraude pymes")
    assert u.startswith("otpauth://totp/Antifraude%20pymes%3AMarlon%20A.%20%28admin%29?")
    assert (
        "secret=ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" in u
        and "issuer=Antifraude%20pymes" in u
        and "digits=6" in u
        and "period=30" in u
        and "algorithm=SHA1" in u
    )
