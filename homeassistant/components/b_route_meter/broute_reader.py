# broute_reader.py

"""BRoute Reader Implementation.

Bルートリーダーの実装.

This module provides a full implementation for scanning the channel,
PANA authentication, and reading E7/E8/E9/EA/EB from a Japanese smart meter
via the B-route interface (ECHONET Lite).

このモジュールは、日本のスマートメーター(Bルート)を介して
ECHONET Lite でチャンネルスキャン、PANA認証、および
E7/E8/E9/EA/EBのデータ取得を行うための実装例を示します。
"""

from datetime import datetime
import logging

from dateutil import tz
import serial

_LOGGER = logging.getLogger(__name__)

# JST/UTC timezones
JST = tz.gettz("Asia/Tokyo")
UTC = tz.gettz("UTC")


class BRouteError(Exception):
    """Generic error for BRoute operations.

    Bルート関連の一般的なエラー.
    """


class BRouteReader:
    """B-Route Reader for E7/E8/E9/EA/EB.

    - This class scans the channel, does PANA authentication in connect(),
      and provides get_data() to read E7/E8/E9/EA/EB from the meter.


    - connect()メソッドでチャンネルスキャンとPANA認証を行い、
      get_data()でメーターからE7/E8/E9/EA/EBを取得します。
    """

    def __init__(self, route_b_id, route_b_pwd, serial_port="/dev/tty.usbserial-120"):
        """Initialize the BRouteReader.

        :param route_b_id: B-route ID (BルートID).
        :param route_b_pwd: B-route password (Bルートパスワード).
        :param serial_port: Serial port device path (シリアルポートパス).
        """
        self.route_b_id = route_b_id
        self.route_b_pwd = route_b_pwd
        self.serial_port_path = serial_port

        self.serial_port = None
        self.scanRes = {}
        self.ipv6Addr = None

    def connect(self):
        """Perform channel scan, PANA authentication, etc.

        チャンネルスキャン、PANA認証などを実行して接続を確立.


        Opens the serial port, sets password/ID, scans the channel,
        obtains IPv6 address, does PANA authentication.
        Raises BRouteError if something fails.


        シリアルポートを開き、パスワード/IDを設定してチャンネルをスキャンし、
        IPv6アドレスを取得した上でPANA認証を行います。
        失敗した場合は BRouteError を投げます。
        """
        try:
            # 1) Open serial port
            _LOGGER.debug("Opening serial port: %s", self.serial_port_path)
            self.serial_port = serial.Serial(
                port=self.serial_port_path,
                baudrate=115200,
                timeout=2,  # 2秒のタイムアウト設定
            )

            # 2) Set B-route password
            self._write_cmd(f"SKSETPWD C {self.route_b_pwd}\r\n")
            self._wait_ok()

            # 3) Set B-route ID
            self._write_cmd(f"SKSETRBID {self.route_b_id}\r\n")
            self._wait_ok()

            # 4) Channel scan
            self._scan_channel()

            # 5) Set channel & PAN ID
            self._write_cmd(f"SKSREG S2 {self.scanRes['Channel']}\r\n")
            self._wait_ok()
            self._write_cmd(f"SKSREG S3 {self.scanRes['Pan ID']}\r\n")
            self._wait_ok()

            # 6) Get IPv6 address
            self._write_cmd(f"SKLL64 {self.scanRes['Addr']}\r\n")
            self.serial_port.readline()  # possibly empty
            line_ipv6 = (
                self.serial_port.readline().decode("utf-8", errors="ignore").strip()
            )
            self.ipv6Addr = line_ipv6
            _LOGGER.debug("IPv6 address: %s", self.ipv6Addr)

            # 7) PANA authentication
            self._write_cmd(f"SKJOIN {self.ipv6Addr}\r\n")
            self._wait_ok()

            # Wait for EVENT 25 (success) or EVENT 24 (fail)
            bConnected = False
            while not bConnected:
                raw_line = self.serial_port.readline()
                if not raw_line:
                    continue
                if raw_line.startswith(b"EVENT 24"):
                    self._handle_pana_failure()
                if raw_line.startswith(b"EVENT 25"):
                    _LOGGER.debug("PANA authentication success. (EVENT 25)")
                    bConnected = True

            _LOGGER.info("B-route connection established successfully.")
        except Exception as e:
            if self.serial_port:
                self.serial_port.close()
            _LOGGER.error("Failed to connect B-route: %s", e)
            raise BRouteError(e) from e

    def _handle_pana_failure(self):
        """Handle PANA authentication failure."""
        raise BRouteError("PANA authentication failed. (EVENT 24)")

    def get_data(self):
        """Read E7/E8/E9/EA/EB data from the meter.

        メーターから E7/E8/E9/EA/EB データを取得.


        - Sends a single ECHONET Lite read request (0x62) for
          E7/E8/E9/EA/EB
        - Parses the response from ERXUDP
        - Returns a dict with keys: e7_power, e8_current, e9_voltage, ea_forward, eb_reverse


        - 1回の ECHONET Lite 読み取りリクエスト(0x62)を
          E7/E8/E9/EA/EB に対して送信
        - ERXUDP の応答を解析
        - e7_power, e8_current, e9_voltage, ea_forward, eb_reverse をキーとする
          dictを返します
        """
        if not self.serial_port or not self.ipv6Addr:
            raise BRouteError("B-route is not connected. Call connect() first.")

        # Build ECHONET Lite frame to read multiple EPC
        # ESV=0x62=ReadRequest, OPC=5
        epcs = [0xE7, 0xE8, 0xE9, 0xEA, 0xEB]
        frame = b"\x10\x81"  # EHD
        frame += b"\x00\x01"  # TID
        frame += b"\x05\xff\x01"  # SEOJ=Controller
        frame += b"\x02\x88\x01"  # DEOJ=低圧スマートメーター
        frame += b"\x62"  # ESV=ReadRequest
        frame += b"\x05"  # OPC=5
        for epc_code in epcs:
            frame += epc_code.to_bytes(1, "big")  # EPC
            frame += b"\x00"  # PDC=0

        cmd_str = (
            f"SKSENDTO 1 {self.ipv6Addr} 0E1A 1 {len(frame):04X} ".encode()
            + frame
            + b"\r\n"
        )
        self.serial_port.write(cmd_str)

        e7_power = None
        e8_current = None
        e9_voltage = None
        ea_val = None
        eb_val = None

        # We'll read up to ~10 lines to look for ERXUDP
        # 10行程度を最大読み込み
        for _ in range(10):
            raw_line = self.serial_port.readline()
            if not raw_line:
                continue

            if raw_line.startswith(b"ERXUDP"):
                # Typically: ERXUDP <...> <ECHONET payload>
                tokens = raw_line.split(b" ", 8)
                if len(tokens) < 9:
                    _LOGGER.warning("ERXUDP line format unexpected.")
                    continue

                echonet_payload = tokens[8].rstrip(b"\r\n")
                frame_info = self._parse_echonet_frame(echonet_payload)
                # parse properties
                for epc, pdc, edt in frame_info.get("properties", []):
                    if epc == 0xE7 and pdc == 4:
                        # E7: instantaneous power (W)
                        val = int.from_bytes(edt, byteorder="big", signed=False)
                        # handle sign bit if needed
                        if (val >> 31) & 0x01 == 1:
                            val = (val ^ 0xFFFFFFFF) * (-1) - 1
                        e7_power = val

                    elif epc == 0xE8:
                        # E8: instantaneous current
                        # often 4 bytes => 2byte + 2byte for two phases
                        # unit is often 0.1 A
                        if pdc == 4:
                            i1 = int.from_bytes(edt[0:2], "big", signed=False)
                            i2 = int.from_bytes(edt[2:4], "big", signed=False)
                            # example: convert to float A
                            # note: if single-phase or 2-wire, confirm actual format
                            # here we sum them for demonstration or keep them separate
                            # We'll pick sum or the maximum for demonstration.
                            # You might prefer to store them separately.
                            phase1 = i1 / 10.0
                            phase2 = i2 / 10.0
                            # If you want a single "combined" current,
                            # you could do something like
                            # e8_current = phase1 + phase2
                            # But let's just store phase1 for simplicity:
                            e8_current = phase1 + phase2
                        else:
                            _LOGGER.debug(
                                "Unexpected E8 format: pdc=%d, edt=%s", pdc, edt
                            )

                    elif epc == 0xE9:
                        # E9: instantaneous voltage
                        if pdc == 4:
                            v1 = int.from_bytes(edt[0:2], "big", signed=False)
                            v2 = int.from_bytes(edt[2:4], "big", signed=False)
                            # Possibly each is 1 V unit, or 0.1 V
                            # We'll just store average for demonstration
                            e9_voltage = (v1 + v2) / 2.0
                        elif pdc == 0:
                            _LOGGER.debug(
                                "Meter does not support E9 or no voltage data."
                            )
                        else:
                            _LOGGER.debug(
                                "Unexpected E9 format: pdc=%d, edt=%s", pdc, edt
                            )

                    elif epc in [0xEA, 0xEB] and pdc >= 10:
                        # EA: forward cumulative
                        # EB: reverse cumulative
                        # Usually includes date/time + 4 bytes for accum
                        year = int.from_bytes(edt[0:2], "big")
                        month = edt[2]
                        day = edt[3]
                        hour = edt[4]
                        minute = edt[5]
                        second = edt[6]
                        accum_raw = int.from_bytes(edt[7:11], "big", signed=False)
                        # assume 0.1 kWh step
                        accum_val = accum_raw / 10.0

                        # we won't use date/time in the final result, but you could store it
                        # for demonstration, we parse it
                        try:
                            dt_jst = datetime(
                                year, month, day, hour, minute, second, tzinfo=JST
                            )
                            dt_utc = dt_jst.astimezone(UTC)
                            _LOGGER.debug("Parsed EA/EB date: %s", dt_utc.isoformat())
                        except ValueError:
                            pass

                        if epc == 0xEA:
                            ea_val = accum_val
                        else:
                            eb_val = accum_val

                # after parsing properties, we can break
                break

        results = {
            "e7_power": e7_power,
            "e8_current": e8_current,
            "e9_voltage": e9_voltage,
            "ea_forward": ea_val,
            "eb_reverse": eb_val,
        }
        _LOGGER.debug("B-route read results: %s", results)
        return results

    # --------------------------------------------------
    # Below are private helper methods
    # 以下はプライベートヘルパーメソッド
    # --------------------------------------------------

    def _scan_channel(self):
        """Scan channel and populate self.scanRes.

        チャンネルスキャンを実施し、self.scanRes に格納.
        """
        _LOGGER.debug("Scanning channel for Smart Meter...")
        scanDuration = 5

        while "Channel" not in self.scanRes:
            cmd_str = f"SKSCAN 2 FFFFFFFF {scanDuration}\r\n"
            self._write_cmd(cmd_str)
            scanEnd = False
            while not scanEnd:
                raw_line = self.serial_port.readline()
                if not raw_line:
                    continue
                if raw_line.startswith(b"EVENT 22"):
                    # scan finished
                    scanEnd = True
                elif raw_line.startswith(b"  "):
                    line_decoded = raw_line.decode("utf-8", errors="ignore").strip()
                    cols = line_decoded.split(":")
                    if len(cols) == 2:
                        key, val = cols
                        self.scanRes[key] = val
            scanDuration += 1
            if scanDuration > 14 and "Channel" not in self.scanRes:
                raise BRouteError("Could not find valid channel within scan duration.")

        _LOGGER.debug("Channel found: %s", self.scanRes.get("Channel"))
        _LOGGER.debug("Pan ID found: %s", self.scanRes.get("Pan ID"))

    def _wait_ok(self):
        """Wait until we see 'OK' in a line.

        'OK'が出るまで待機.
        """
        empty_count = 0
        max_empty_read = 5
        while True:
            raw_line = self.serial_port.readline()
            if not raw_line:
                empty_count += 1
                if empty_count >= max_empty_read:
                    raise BRouteError("wait_ok() timed out / too many empty reads.")
                continue
            if raw_line.startswith(b"OK"):
                break

    def _write_cmd(self, cmd_str):
        """Write command to serial, log debug."""
        if isinstance(cmd_str, str):
            cmd_str = cmd_str.encode()
        _LOGGER.debug("Write to meter: %s", cmd_str)
        self.serial_port.write(cmd_str)

    def _parse_echonet_frame(self, echonet_bytes):
        """Parse ECHONET Lite frame.

        ECHONET Liteフレームを解析.

        Return a dict: { EHD, TID, SEOJ, DEOJ, ESV, OPC, properties=[(EPC,PDC,EDT),...] }
        """
        result = {}
        if len(echonet_bytes) < 12:
            return result

        EHD = echonet_bytes[0:2]
        TID = echonet_bytes[2:4]
        SEOJ = echonet_bytes[4:7]
        DEOJ = echonet_bytes[7:10]
        ESV = echonet_bytes[10]
        OPC = echonet_bytes[11]

        result["EHD"] = EHD
        result["TID"] = TID
        result["SEOJ"] = SEOJ
        result["DEOJ"] = DEOJ
        result["ESV"] = ESV
        result["OPC"] = OPC
        result["properties"] = []

        offset = 12
        for _ in range(OPC):
            if offset + 2 > len(echonet_bytes):
                break
            EPC = echonet_bytes[offset]
            PDC = echonet_bytes[offset + 1]
            offset += 2
            if offset + PDC > len(echonet_bytes):
                break
            EDT = echonet_bytes[offset : offset + PDC]
            offset += PDC
            result["properties"].append((EPC, PDC, EDT))

        return result
