"""Sensor platform for B-Route Smart Meter.

Bルートスマートメーターのセンサープラットフォーム.

Defines the sensor entities that read E7/E8/E9/EA/EB data from
the B-route meter using a DataUpdateCoordinator.

BルートメーターからE7/E8/E9/EA/EBデータを取得するセンサーエンティティを
DataUpdateCoordinatorを使用して定義します。

"""

from datetime import timedelta
import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .broute_reader import BRouteReader
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Sensor descriptions for E7/E8/E9/EA/EB
# E7/E8/E9/EA/EB向けのSensorEntityDescriptionを定義
# -----------------------------------------------------------------------------

SENSOR_TYPES: list[SensorEntityDescription] = [
    SensorEntityDescription(
        key="e7_power",
        name="B-Route Instantaneous Power",
        icon="mdi:flash",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="e8_current",
        name="B-Route Instantaneous Current",
        icon="mdi:current-ac",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="e9_voltage",
        name="B-Route Instantaneous Voltage",
        icon="mdi:power-plug",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="ea_forward",
        name="B-Route Cumulative Forward",
        icon="mdi:gauge",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="eb_reverse",
        name="B-Route Cumulative Reverse",
        icon="mdi:gauge",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up B-Route meter sensors based on a config entry."""
    data = entry.data
    route_b_id = data["route_b_id"]
    route_b_pwd = data["route_b_pwd"]
    serial_port = data.get("serial_port", "/dev/ttyS0")

    # Create a DataUpdateCoordinator to manage periodic fetch
    coordinator = BRouteDataCoordinator(hass, route_b_id, route_b_pwd, serial_port)
    # 1回目の読み取りを行う
    await coordinator.async_config_entry_first_refresh()

    # Create sensor entities for each SensorEntityDescription
    sensors = [
        BRouteSensorEntity(coordinator, description) for description in SENSOR_TYPES
    ]

    async_add_entities(sensors)


class BRouteDataCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch data from B-route meter.

    Bルートメーターからデータを取得するコーディネーター.

    Schedules regular data fetch. We'll store or reuse the BRouteReader,
    and run its get_data() in a thread pool.

    一定間隔でBルートメーターからデータを取得し、取得結果を他のエンティティと共有します。
    BRouteReaderを保持して、get_data()をスレッドプールで実行します。
    """

    def __init__(self, hass, route_b_id, route_b_pwd, serial_port) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="B-Route Meter Coordinator",
            update_interval=timedelta(seconds=10),  # update every 10s
        )
        self.reader = BRouteReader(route_b_id, route_b_pwd, serial_port)

        # Optionally do an initial connect once
        # 必要に応じて初回接続を実行
        self.reader.connect()

    async def _async_update_data(self):
        try:
            # run get_data in a thread pool so it won't block the event loop
            # get_dataをスレッドプールで実行してイベントループをブロックしない
            return await self.hass.async_add_executor_job(self.reader.get_data)
        except OSError as err:
            raise UpdateFailed(f"B-Route meter update failed: {err}") from err


class BRouteSensorEntity(SensorEntity):
    """B-Route sensor entity referencing a SensorEntityDescription.

    SensorEntityDescriptionを参照するBルートセンサーエンティティ.

    We store a reference to the DataUpdateCoordinator and a SensorEntityDescription,
    and we get the current sensor value from coordinator.data.

    DataUpdateCoordinatorとSensorEntityDescriptionを参照し、
    coordinator.dataから現在値を取得してセンサーとして公開します。
    """

    def __init__(
        self,
        coordinator: BRouteDataCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        self._coordinator = coordinator
        self.entity_description = description

        # Generate a unique_id from the description's key
        self._attr_unique_id = f"b_route_{description.key}"
        self._last_state = None

    @property
    def should_poll(self) -> bool:
        """Return False to indicate that we should not be polled for updates."""
        return False

    @property
    def available(self) -> bool:
        """Return True if coordinator last update was successful."""
        return self._coordinator.last_update_success

    @property
    def native_value(self) -> float | None:
        """Return the sensor's native value."""
        data = self._coordinator.data
        if not data:
            return None
        # The "key" in description matches the dict key in data
        value = data.get(self.entity_description.key)
        if value is None:
            return self._last_state
        self._last_state = value
        return value

    @property
    def device_info(self) -> DeviceInfo | None:
        """Optional device info.

        任意のデバイス情報.
        """

        return {
            "identifiers": {(DOMAIN, "b_route_meter_device")},
            "name": "B-Route Smart Meter",
            "manufacturer": "ROHM Co., Ltd.",
            "model": "BP35A1",
        }

    async def async_added_to_hass(self):
        """Register update listener when entity is added.

        エンティティが追加された際に、コーディネーターにリスナーを登録.
        """

        self._coordinator.async_add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        """Remove update listener when entity is removed.

        エンティティが削除される際に、リスナーを解除.
        """

        self._coordinator.async_remove_listener(self.async_write_ha_state)
