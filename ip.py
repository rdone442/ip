import socket
import requests
import geoip2.database
import os
import sys
from dotenv import load_dotenv
import datetime
from urllib.parse import quote
import time
import concurrent.futures

# 修改全局变量来优化执行时间
LAST_API_CALL = 0
MIN_INTERVAL = 1.0  # 减少等待时间
MAX_RETRIES = 2    # 减少重试次数
MAX_WORKERS = 5    # 并发查询数量

def ensure_dir(directory):
    """确保目录存在，如果不存在则创建"""
    if not os.path.exists(directory):
        os.makedirs(directory)

def normalize_ip(ip):
    """规范化IP地址，处理最后一段大于255的情况"""
    try:
        # 移除IPv6的方括号
        ip = ip.strip('[]')
        # 分割IP地址
        parts = ip.split('.')
        if len(parts) != 4:
            return None
            
        # 转换前三段
        for i in range(3):
            if not (0 <= int(parts[i]) <= 255):
                return None
                
        # 处理最后一段
        last_num = int(parts[3])
        if last_num > 255:
            # 计算进位
            carry = last_num // 256
            remainder = last_num % 256
            # 更新第三段
            parts[2] = str(int(parts[2]) + carry)
            # 如果第三段超过255，则IP无效
            if int(parts[2]) > 255:
                return None
            # 更新最后一段
            parts[3] = str(remainder)
            
        return '.'.join(parts)
    except (ValueError, AttributeError):
        return None

def is_valid_ip(ip):
    """验证IP地址格式是否有效，支持Cloudflare特殊格式"""
    try:
        # 移除IPv6的方括号
        ip = ip.strip('[]')
        # 分割IP地址
        parts = ip.split('.')
        # 检查IPv4格式
        if len(parts) != 4:
            return False
            
        # 检查前三段是否在0-255范围内
        for i in range(3):
            if not (0 <= int(parts[i]) <= 255):
                return False
                
        # 检查最后一段
        last_num = int(parts[3])
        if last_num <= 255:
            return True
            
        # 如果最后一段大于255，检查是否可以规范化
        normalized_ip = normalize_ip(ip)
        return normalized_ip is not None
            
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

def wait_for_api():
    """等待足够的时间间隔再发送下一个请求"""
    global LAST_API_CALL
    current_time = time.time()
    if current_time - LAST_API_CALL < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - (current_time - LAST_API_CALL))
    LAST_API_CALL = time.time()

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
    retries = 0
    while retries < MAX_RETRIES:
        try:
            print(f"[尝试在线查询] IP: {ip} (尝试 {retries + 1}/{MAX_RETRIES})")
            wait_for_api()  # 等待适当的时间间隔
            response = requests.get(f"http://ip-api.com/json/{ip}", timeout=3)
            
            # 处理429状态码
            if response.status_code == 429:
                retries += 1
                if retries < MAX_RETRIES:
                    wait_time = (2 ** retries) * MIN_INTERVAL  # 指数退避
                    print(f"[速率限制] 等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
                    continue
                else:
                    print(f"[在线查询失败] IP: {ip} 达到最大重试次数")
                    return "XX"
            
            # 检查其他状态码
            if response.status_code != 200:
                print(f"[在线查询失败] IP: {ip} HTTP状态码: {response.status_code}")
                return "XX"
            
            # 检查响应内容是否为空
            if not response.text.strip():
                print(f"[在线查询失败] IP: {ip} 响应为空")
                return "XX"
            
            try:
                data = response.json()
            except ValueError as e:
                print(f"[在线查询失败] IP: {ip} JSON解析错误: {str(e)}")
                print(f"响应内容: {response.text[:200]}")  # 只打印前200个字符
                return "XX"
            
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
            
    return "XX"  # 所有重试都失败后返回XX

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

def batch_process_ips(ip_list, reader, ports):
    """批量处理IP地址"""
    results = []
    country_results = {}
    
    # 使用线程池并发处理IP
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # 创建任务
        future_to_ip = {executor.submit(process_single_ip, ip, reader, ports): ip for ip in ip_list}
        
        # 处理结果
        for future in concurrent.futures.as_completed(future_to_ip):
            ip = future_to_ip[future]
            try:
                ip_results = future.result()
                if ip_results:
                    for result in ip_results:
                        results.append(result)
                        # 解析结果获取国家代码
                        country_code = result.split('#')[-1]
                        if country_code not in country_results:
                            country_results[country_code] = []
                        country_results[country_code].append(result)
            except Exception as e:
                print(f"处理IP {ip} 时发生错误: {str(e)}")
                
    return results, country_results

def process_single_ip(ip, reader, ports):
    """处理单个IP地址"""
    results = []
    try:
        ip = ip.strip()
        if not ip:
            return results
            
        # 添加IP验证和规范化
        if not is_valid_ip(ip):
            print(f"[URL读取] 跳过无效IP: {ip}")
            return results
            
        # 规范化IP地址
        normalized_ip = normalize_ip(ip) or ip
        country_code = get_country_code(normalized_ip.strip('[]'), reader)
        
        # 为每个端口生成结果
        for port in ports:
            result = f'{normalized_ip}:{port}#{country_code}'
            results.append(result)
            if ip != normalized_ip:
                print(f"[URL读取] {result} (原始IP: {ip})")
            else:
                print(f"[URL读取] {result}")
                
    except Exception as e:
        print(f"处理IP {ip} 时发生错误: {str(e)}")
        
    return results

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
        
        # 获取端口列表
        ports = os.environ.get('TARGET_PORTS', '443').split(',')
        ports = [port.strip() for port in ports if port.strip().isdigit()]
        if not ports:
            print('警告：未设置有效的 TARGET_PORTS 环境变量，使用默认端口443')
            ports = ['443']
        
        all_ips = set()  # 使用集合去重
        
        # 从所有URL收集IP
        for url in urls:
            try:
                encoded_url = quote(url, safe=':/?=')
                print(f"\n[URL读取] 正在从 {url} 获取IP列表...")
                response = requests.get(encoded_url, timeout=10)
                response.raise_for_status()
                
                ip_list = response.text.strip().split()
                all_ips.update(ip.strip() for ip in ip_list if ip.strip())
                
            except Exception as e:
                print(f'处理URL {url} 时发生错误: {str(e)}')
                continue
        
        # 批量处理收集到的所有IP
        results, country_results = batch_process_ips(list(all_ips), reader, ports)
        
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
