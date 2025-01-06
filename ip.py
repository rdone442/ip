import socket
import requests
import geoip2.database
import os
import sys
from dotenv import load_dotenv
import datetime

def ensure_dir(directory):
    """确保目录存在，如果不存在则创建"""
    if not os.path.exists(directory):
        os.makedirs(directory)

def is_valid_ip(ip):
    """验证IP地址格式是否有效"""
    try:
        # 移除IPv6的方括号
        ip = ip.strip('[]')
        # 分割IP地址
        parts = ip.split('.')
        # 检查IPv4格式
        if len(parts) != 4:
            return False
        # 检查每个部分是否在0-255范围内
        return all(0 <= int(part) <= 255 for part in parts)
    except (ValueError, AttributeError):
        return False

def download_mmdb():
    """下载MaxMind GeoIP2数据库"""
    try:
        data_dir = "data"
        ensure_dir(data_dir)
        db_path = os.path.join(data_dir, "GeoLite2-Country.mmdb")
        
        # 如果文件不存在或强制更新，则下载
        if not os.path.exists(db_path) or os.environ.get('FORCE_UPDATE') == 'true':
            print("正在下载数据库...")
            url = "https://raw.githubusercontent.com/Loyalsoldier/geoip/release/GeoLite2-Country.mmdb"
            response = requests.get(url)
            
            with open(db_path, "wb") as f:
                f.write(response.content)
                
            print("GeoIP2数据库更新成功")
        else:
            # 检查当前时间
            current_hour = datetime.datetime.now().hour
            if os.environ.get('GITHUB_ACTIONS') and current_hour != 10:  # 不是北京时间10点
                print("不在数据库更新时间，跳过下载")
            else:
                print("正在更新数据库...")
                url = "https://raw.githubusercontent.com/Loyalsoldier/geoip/release/GeoLite2-Country.mmdb"
                response = requests.get(url)
                
                with open(db_path, "wb") as f:
                    f.write(response.content)
                    
                print("GeoIP2数据库更新成功")
            
        return db_path
    except Exception as e:
        print(f"下载GeoIP2数据库失败: {str(e)}")
        sys.exit(1)

def get_country_code(ip, reader):
    """查询IP所属国家代码，先用数据库，失败后用ip-api.com"""
    # 首先尝试使用GeoIP2数据库
    try:
        response = reader.country(ip)
        country_code = response.country.iso_code
        if country_code:
            print(f"[数据库查询成功] IP: {ip} 国家代码: {country_code}")
            return country_code
    except Exception as e:
        print(f"[数据库查询失败] IP: {ip} 错误信息: {str(e)}")
    
    # 数据库查询失败，尝试使用ip-api.com
    try:
        print(f"[尝试在线查询] IP: {ip}")
        response = requests.get(f"http://ip-api.com/json/{ip}", timeout=3)
        data = response.json()
        
        if data.get("status") == "success":
            country_code = data.get("countryCode", "XX")
            print(f"[在线查询成功] IP: {ip} 国家代码: {country_code}")
            return country_code
        else:
            print(f"[在线查询失败] IP: {ip} 错误信息: {data.get('message', '未知错误')}")
            return "XX"
            
    except requests.exceptions.Timeout:
        print(f"[在线查询超时] IP: {ip}")
        return "XX"
    except Exception as e:
        print(f"[在线查询错误] IP: {ip} 错误信息: {str(e)}")
        return "XX"

def resolve_domain(reader):
    """解析域名获取IP"""
    try:
        # 检查必需的环境变量
        if 'TARGET_DOMAIN' not in os.environ:
            print('错误：未设置 TARGET_DOMAIN 环境变量')
            return []
            
        domains = os.environ['TARGET_DOMAIN'].split(',')
        domains = [domain.strip() for domain in domains if domain.strip()]
        
        if not domains:
            print('错误：TARGET_DOMAIN 环境变量为空')
            return []
        
        ports = os.environ.get('TARGET_PORTS', '443').split(',')
        ports = [port.strip() for port in ports if port.strip().isdigit()]
        
        if not ports:
            print('警告：未设置有效的 TARGET_PORTS 环境变量，使用默认端口443')
            ports = ['443']
        
        results = []
        country_results = {}  # 使用字典存储不同国家的结果
        
        # 处理每个域名
        for domain in domains:
            try:
                print(f"\n[域名解析] 正在解析域名: {domain}")
                addrinfo = socket.getaddrinfo(domain, None)
                all_ips = set()
                
                for addr in addrinfo:
                    ip = addr[4][0]
                    if ':' in ip:
                        all_ips.add(f'[{ip}]')
                    else:
                        all_ips.add(ip)
                
                for ip in sorted(all_ips):
                    country_code = get_country_code(ip.strip('[]'), reader)
                    for port in ports:
                        result = f'{ip}:{port}#{country_code}'
                        results.append(result)
                        print(f"[域名解析] {result}")
                        
                        # 将结果添加到对应国家的列表中
                        if country_code not in country_results:
                            country_results[country_code] = []
                        country_results[country_code].append(result)
                        
            except socket.gaierror as e:
                print(f'DNS解析错误 {domain}: {str(e)}')
                continue
            except Exception as e:
                print(f'域名解析发生错误 {domain}: {str(e)}')
                continue
        
        # 确保ip目录存在
        ip_dir = "ip"
        ensure_dir(ip_dir)
        
        # 为每个国家创建单独的文件
        for country_code, country_ips in country_results.items():
            if country_code == "XX":  # 跳过未知国家
                continue
                
            filename = os.path.join(ip_dir, f'{country_code.lower()}.txt')
            with open(filename, 'a', encoding='utf-8') as f:  # 使用追加模式
                for result in country_ips:
                    f.write(f'{result}\n')
            print(f"\n[域名解析] 发现 {len(country_ips)} 个 {country_code} 地址，已保存到 {filename}")
                
        return results
            
    except Exception as e:
        print(f'域名解析发生错误: {str(e)}')
        return []

def read_ip_from_url(reader):
    """从多个URL读取IP列表并查询国家代码"""
    try:
        # 获取URL列表
        if 'TARGET_URLS' not in os.environ:
            print('错误：未设置 TARGET_URLS 环境变量')
            return []
            
        urls = os.environ['TARGET_URLS'].split(',')
        urls = [url.strip() for url in urls if url.strip()]
        
        if not urls:
            print('错误：TARGET_URLS 环境变量为空')
            return []
        
        results = []
        country_results = {}  # 使用字典存储不同国家的结果
        
        # 获取端口列表，如果环境变量未设置则使用默认端口443
        ports = os.environ.get('TARGET_PORTS', '443').split(',')
        ports = [port.strip() for port in ports if port.strip().isdigit()]
        if not ports:
            print('警告：未设置有效的 TARGET_PORTS 环境变量，使用默认端口443')
            ports = ['443']
        
        # 处理每个URL
        for url in urls:
            try:
                print(f"\n[URL读取] 正在从 {url} 获取IP列表...")
                response = requests.get(url, timeout=10)  # 添加超时设置
                response.raise_for_status()
                
                ip_list = response.text.strip().split()
                
                for ip in ip_list:
                    ip = ip.strip()
                    if not ip:
                        continue
                        
                    # 添加IP验证
                    if not is_valid_ip(ip):
                        print(f"[URL读取] 跳过无效IP: {ip}")
                        continue
                        
                    country_code = get_country_code(ip, reader)
                    # 为每个端口生成一个结果
                    for port in ports:
                        result = f'{ip}:{port}#{country_code}'
                        results.append(result)
                        print(f"[URL读取] {result}")
                        
                        # 将结果添加到对应国家的列表中
                        if country_code not in country_results:
                            country_results[country_code] = []
                        country_results[country_code].append(result)
                        
            except requests.RequestException as e:
                print(f'获取URL {url} 失败: {str(e)}')
                continue
            except Exception as e:
                print(f'处理URL {url} 时发生错误: {str(e)}')
                continue
        
        # 确保ip目录存在
        ip_dir = "ip"
        ensure_dir(ip_dir)
        
        # 为每个国家创建单独的文件
        for country_code, country_ips in country_results.items():
            if country_code == "XX":  # 跳过未知国家
                continue
                
            filename = os.path.join(ip_dir, f'{country_code.lower()}.txt')
            with open(filename, 'w', encoding='utf-8') as f:
                for result in country_ips:
                    f.write(f'{result}\n')
            print(f"\n[URL读取] 发现 {len(country_ips)} 个 {country_code} 地址，已保存到 {filename}")
                
        return results
            
    except Exception as e:
        print(f'URL读取发生错误: {str(e)}')
        return []

def main():
    """主函数，自动检测条件并执行"""
    print("正在初始化...")
    
    # 加载环境变量
    if not os.environ.get('GITHUB_ACTIONS'):
        load_dotenv()
    
    # 检查条件
    has_domain = 'TARGET_DOMAIN' in os.environ
    
    # 严格检查环境变量
    if has_domain and 'TARGET_DOMAIN' not in os.environ:
        print('错误：未设置 TARGET_DOMAIN 环境变量')
        has_domain = False
    
    if not has_domain:
        print("提示：未设置域名环境变量，将只从GitHub获取IP列表")
    
    # 准备GeoIP2数据库
    db_path = os.path.join("data", "GeoLite2-Country.mmdb")
    if not os.path.exists(db_path):
        print("数据库文件不存在，正在下载...")
        db_path = download_mmdb()
    else:
        # 检查文件大小确保不是空文件
        if os.path.getsize(db_path) == 0:
            print("数据库文件损坏，重新下载...")
            db_path = download_mmdb()
    
    reader = geoip2.database.Reader(db_path)
    all_results = []
    
    try:
        # 确保ip目录存在
        ip_dir = "ip"
        ensure_dir(ip_dir)
        
        # 执行域名解析
        if has_domain:
            if 'TARGET_DOMAIN' not in os.environ:
                print('错误：未设置 TARGET_DOMAIN 环境变量')
            else:
                domain_results = resolve_domain(reader)
                all_results.extend(domain_results)
        
        # 从GitHub读取IP列表
        url_results = read_ip_from_url(reader)
        all_results.extend(url_results)
        
        # 保存所有结果到ip.txt
        if all_results:
            with open(os.path.join(ip_dir, 'ip.txt'), 'w', encoding='utf-8') as f:
                for result in all_results:
                    f.write(f'{result}\n')
        
    finally:
        reader.close()
    
    print("\n处理完成！")
    print(f"所有结果已保存到 ip/ip.txt")

if __name__ == '__main__':
    main()