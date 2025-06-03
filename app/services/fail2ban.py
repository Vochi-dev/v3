import subprocess
import json
import aiohttp
from typing import List, Dict

async def get_banned_ips() -> List[Dict[str, str]]:
    """Get list of banned IPs from fail2ban with country information"""
    try:
        # Get list of all jails first
        result = subprocess.run(['fail2ban-client', 'status'], 
                              capture_output=True, text=True)
        
        # Parse jails from output
        jails = []
        for line in result.stdout.split('\n'):
            if line.startswith('Jail list:'): 
                jails = line.split(':')[1].strip().split()
                break
        
        # Get banned IPs from all jails
        banned_ips = set()  # используем set для уникальных IP
        for jail in jails:
            result = subprocess.run(['fail2ban-client', 'status', jail], 
                                  capture_output=True, text=True)
            
            output = result.stdout
            for line in output.split('\n'):
                if 'Banned IP list:' in line:
                    ips = line.split(':')[1].strip().split()
                    banned_ips.update(ips)  # добавляем уникальные IP
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
    """Get total count of banned IPs from all jails"""
    try:
        # Get list of all jails first
        result = subprocess.run(['fail2ban-client', 'status'], 
                              capture_output=True, text=True)
        
        # Parse jails from output
        jails = []
        for line in result.stdout.split('\n'):
            if line.startswith('Jail list:'): 
                jails = line.split(':')[1].strip().split()
                break
        
        # Get total banned count from all jails
        total_banned = 0
        for jail in jails:
            result = subprocess.run(['fail2ban-client', 'status', jail], 
                                  capture_output=True, text=True)
            
            output = result.stdout
            for line in output.split('\n'):
                if 'Currently banned:' in line:
                    count = int(line.split(':')[1].strip())
                    total_banned += count
                    break
                    
        return total_banned
    except Exception as e:
        print(f"Error getting banned count: {e}")
        return 0 