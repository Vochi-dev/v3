import subprocess
import json
import aiohttp
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

async def get_banned_ips() -> List[Dict[str, str]]:
    """Get list of banned IPs from fail2ban with country information"""
    try:
        # Get list of all jails first
        result = subprocess.run(['sudo', 'fail2ban-client', 'status'], 
                              capture_output=True, text=True,
                              env={'PYTHONUNBUFFERED': '1'})
        
        logger.debug("Initial fail2ban status output: %s", result.stdout)
        
        # Parse jails from output
        jails = []
        for line in result.stdout.split('\n'):
            if 'Jail list:' in line:
                # Строка имеет вид: `- Jail list:   jail1, jail2`
                jails = line.split(':', 1)[1].strip().split(',')
                jails = [j.strip() for j in jails]
                break
        
        logger.debug("Found jails: %s", jails)
        
        # Get banned IPs from all jails
        banned_ips = set()  # используем set для уникальных IP
        for jail in jails:
            result = subprocess.run(['sudo', 'fail2ban-client', 'status', jail], 
                                  capture_output=True, text=True)
            
            logger.debug("Status for jail %s: %s", jail, result.stdout)
            
            output = result.stdout
            for line in output.split('\n'):
                if 'Banned IP list:' in line:
                    ips = line.split(':', 1)[1].strip().split()
                    banned_ips.update(ips)  # добавляем уникальные IP
                    logger.debug("Added IPs from jail %s: %s", jail, ips)
                    break
        
        logger.debug("Total unique banned IPs: %s", banned_ips)
        
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
                except Exception as e:
                    logger.error("Error getting info for IP %s: %s", ip, str(e))
                    banned_info.append({
                        'ip': ip,
                        'country': 'Error',
                        'city': 'Error',
                        'region': 'Error'
                    })
                    
        return banned_info
    except Exception as e:
        logger.error("Error getting banned IPs: %s", str(e))
        return []

async def get_banned_count() -> int:
    """Get total count of banned IPs from all jails"""
    try:
        # Get list of all jails first
        result = subprocess.run(['sudo', 'fail2ban-client', 'status'], 
                              capture_output=True, text=True,
                              env={'PYTHONUNBUFFERED': '1'})
        
        logger.debug("Initial fail2ban status output: %s", result.stdout)
        if result.stderr:
            logger.error("fail2ban-client stderr: %s", result.stderr)
        
        # Parse jails from output
        jails = []
        for line in result.stdout.split('\n'):
            if 'Jail list:' in line:
                # Строка имеет вид: `- Jail list:   jail1, jail2`
                jails = line.split(':', 1)[1].strip().split(',')
                jails = [j.strip() for j in jails]
                break
        
        logger.debug("Found jails: %s", jails)
        
        # Get total banned count from all jails
        total_banned = 0
        for jail in jails:
            result = subprocess.run(['sudo', 'fail2ban-client', 'status', jail], 
                                  capture_output=True, text=True)
            
            logger.debug("Status for jail %s: %s", jail, result.stdout)
            if result.stderr:
                logger.error("fail2ban-client stderr for jail %s: %s", jail, result.stderr)
            
            output = result.stdout
            for line in output.split('\n'):
                if 'Currently banned:' in line:
                    count = int(line.split(':', 1)[1].strip())
                    total_banned += count
                    logger.debug("Added %d banned IPs from jail %s", count, jail)
                    break
        
        logger.debug("Total banned count: %d", total_banned)
        return total_banned
    except Exception as e:
        logger.error("Error getting banned count: %s", str(e), exc_info=True)
        return 0 