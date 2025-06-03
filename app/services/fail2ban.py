import subprocess
import json
import aiohttp
from typing import List, Dict

async def get_banned_ips() -> List[Dict[str, str]]:
    """Get list of banned IPs from fail2ban with country information"""
    try:
        # Get banned IPs from fail2ban
        result = subprocess.run(['fail2ban-client', 'status', 'asterisk-webhook'], 
                              capture_output=True, text=True)
        
        # Parse the output to get IPs
        output = result.stdout
        banned_ips = []
        for line in output.split('\n'):
            if 'Banned IP list:' in line:
                ips = line.split(':')[1].strip().split()
                banned_ips.extend(ips)
                break
        
        # Get country information for each IP
        banned_info = []
        async with aiohttp.ClientSession() as session:
            for ip in banned_ips:
                try:
                    async with session.get(f'http://ip-api.com/json/{ip}') as response:
                        if response.status == 200:
                            data = await response.json()
                            banned_info.append({
                                'ip': ip,
                                'country': data.get('country', 'Unknown'),
                                'city': data.get('city', 'Unknown'),
                                'region': data.get('regionName', 'Unknown')
                            })
                        else:
                            banned_info.append({
                                'ip': ip,
                                'country': 'Unknown',
                                'city': 'Unknown',
                                'region': 'Unknown'
                            })
                except:
                    banned_info.append({
                        'ip': ip,
                        'country': 'Error',
                        'city': 'Error',
                        'region': 'Error'
                    })
                    
        return banned_info
    except Exception as e:
        print(f"Error getting banned IPs: {e}")
        return []

async def get_banned_count() -> int:
    """Get count of banned IPs from fail2ban"""
    try:
        result = subprocess.run(['fail2ban-client', 'status', 'asterisk-webhook'], 
                              capture_output=True, text=True)
        
        output = result.stdout
        for line in output.split('\n'):
            if 'Currently banned:' in line:
                return int(line.split(':')[1].strip())
        return 0
    except Exception as e:
        print(f"Error getting banned count: {e}")
        return 0 