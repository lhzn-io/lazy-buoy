from vedirect.vedirect import Vedirect

ve = Vedirect(port='/dev/ttyUSB0', timeout=5)
data = ve.read_data_single()

print("\n--- MPPT Power Data ---")
print(f"Battery Voltage: {int(data.get('V', 0))/1000:.2f} V")
print(f"Battery Current: {int(data.get('I', 0))} mA")
print(f"Panel Power:     {data.get('PPV', 0)} W")
print(f"Load Current:    {data.get('IL', 0)} mA")
print(f"State:           {data.get('CS', 'Unknown')} (0=Off, 3=Bulk/Abs)")
print("-----------------------\n")
