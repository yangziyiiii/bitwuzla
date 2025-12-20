import os
import time
import subprocess
import glob
import csv
import concurrent.futures
from datetime import datetime

# ================= 配置区域 =================
# 1. Oracle Solver (基准，被认为是正确的)
ORACLE_BIN = os.path.expanduser("~/data2/ziyi/bitwuzla/build/src/main/bitwuzla")
ORACLE_NAME = "bzla-official"

# 2. Test Solver (待测试对象)
TARGET_BIN = os.path.expanduser("~/data2/ziyi/bzla-agent/build/src/main/bitwuzla")
TARGET_NAME = "bzla-ziyi"

# 3. Benchmark 目录
BENCHMARK_DIR = "/data/ziyi/data2/ziyi/bzla-agent/smt-comp25/20250315-BMC_WMM"

# 4. 运行参数
MAX_WORKERS = 10       # 最大并发进程数
TIMEOUT_SEC = 1200     # 单个任务超时时间 (秒)
OUTPUT_CSV = "comparison_result.csv" # 结果保存文件

# ===========================================

# ANSI 颜色代码，用于终端高亮
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'

def run_single_solver(binary_path, file_path, timeout):
    """
    运行单个 Solver 并捕获结果和时间
    """
    start_time = time.time()
    try:
        # 运行命令，捕获 stdout 和 stderr
        # 假设 solver 接受文件路径作为第一个参数
        process = subprocess.run(
            [binary_path, file_path],
            capture_output=True,
            text=True,     # 以文本模式运行，便于字符串处理
            timeout=timeout
        )
        duration = time.time() - start_time
        return {
            'status': 'FINISHED',
            'output': process.stdout.strip(), # 去除首尾空白
            'error': process.stderr.strip(),
            'time': duration,
            'return_code': process.returncode
        }
    except subprocess.TimeoutExpired:
        return {
            'status': 'TIMEOUT',
            'output': '',
            'error': 'Process timed out',
            'time': timeout,
            'return_code': -1
        }
    except Exception as e:
        return {
            'status': 'ERROR',
            'output': '',
            'error': str(e),
            'time': 0,
            'return_code': -1
        }

def process_benchmark(file_path):
    """
    处理单个 benchmark 文件：对比两个 solver
    """
    file_name = os.path.basename(file_path)
    
    # 1. 跑 Oracle (Official)
    res_oracle = run_single_solver(ORACLE_BIN, file_path, TIMEOUT_SEC)
    
    # 2. 跑 Target (Ziyi)
    res_target = run_single_solver(TARGET_BIN, file_path, TIMEOUT_SEC)

    # 3. 判定逻辑
    comparison_status = "UNKNOWN"
    
    # 逻辑判断
    if res_oracle['status'] == 'TIMEOUT':
        comparison_status = "ORACLE_TIMEOUT" # 基准都超时了，无法判定对错
    elif res_oracle['status'] == 'ERROR':
        comparison_status = "ORACLE_ERROR"
    elif res_target['status'] == 'TIMEOUT':
        comparison_status = "ZIYI_TIMEOUT"
    elif res_target['status'] == 'ERROR':
        comparison_status = "ZIYI_ERROR"
    else:
        # 两者都正常结束，比较输出
        # 注意：这里进行严格字符串比对。
        # 如果 output 包含 "sat" / "unsat"，这通常是有效的。
        if res_oracle['output'] == res_target['output']:
            comparison_status = "MATCH"
        else:
            comparison_status = "MISMATCH" # 结果不一致，Ziyi 失败

    return {
        'filename': file_name,
        'status': comparison_status,
        'oracle_time': round(res_oracle['time'], 4),
        'target_time': round(res_target['time'], 4),
        'oracle_result': res_oracle['output'].replace('\n', ' '), # 替换换行符防止破坏CSV格式
        'target_result': res_target['output'].replace('\n', ' '),
        'time_diff': round(res_oracle['time'] - res_target['time'], 4) # 正数表示 Ziyi 更快
    }

def main():
    # 检查 Solver 是否存在
    if not os.path.isfile(ORACLE_BIN):
        print(f"{Colors.FAIL}Error: Oracle binary not found at {ORACLE_BIN}{Colors.ENDC}")
        return
    if not os.path.isfile(TARGET_BIN):
        print(f"{Colors.FAIL}Error: Target binary not found at {TARGET_BIN}{Colors.ENDC}")
        return

    # 获取所有 .smt2 文件
    smt2_files = glob.glob(os.path.join(BENCHMARK_DIR, "*.smt2"))
    smt2_files.sort()
    
    total_files = len(smt2_files)
    print(f"{Colors.HEADER}Start Benchmarking...{Colors.ENDC}")
    print(f"Benchmark Dir: {BENCHMARK_DIR}")
    print(f"Total Files: {total_files}")
    print(f"Max Workers: {MAX_WORKERS}")
    print(f"Timeout: {TIMEOUT_SEC}s")
    print("-" * 60)

    results = []
    
    # 初始化 CSV
    with open(OUTPUT_CSV, 'w', newline='') as csvfile:
        fieldnames = ['filename', 'status', 'oracle_time', 'target_time', 'time_diff', 'oracle_result', 'target_result']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        # 并发执行
        with concurrent.futures.ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # 提交任务
            future_to_file = {executor.submit(process_benchmark, f): f for f in smt2_files}
            
            completed_count = 0
            
            for future in concurrent.futures.as_completed(future_to_file):
                data = future.result()
                results.append(data)
                
                # 写入 CSV (实时写入，防止崩溃丢失)
                writer.writerow(data)
                csvfile.flush()
                
                completed_count += 1
                
                # 终端打印格式化输出
                status_color = Colors.OKGREEN if data['status'] == "MATCH" else Colors.FAIL
                if "TIMEOUT" in data['status']:
                    status_color = Colors.WARNING
                
                print(f"[{completed_count}/{total_files}] {data['filename']} -> {status_color}{data['status']}{Colors.ENDC}")
                print(f"   Time: {ORACLE_NAME}={data['oracle_time']}s | {TARGET_NAME}={data['target_time']}s")
                if data['status'] == "MISMATCH":
                    print(f"   {Colors.FAIL}!!! OUTPUT MISMATCH !!!{Colors.ENDC}")
                    print(f"   Oracle: {data['oracle_result'][:50]}...")
                    print(f"   Ziyi  : {data['target_result'][:50]}...")
                print("-" * 40)

    print(f"\n{Colors.HEADER}Benchmark Finished! Results saved to {OUTPUT_CSV}{Colors.ENDC}")
    
    # 简单统计
    matches = sum(1 for r in results if r['status'] == 'MATCH')
    mismatches = sum(1 for r in results if r['status'] == 'MISMATCH')
    timeouts = sum(1 for r in results if 'TIMEOUT' in r['status'])
    
    print(f"Summary:")
    print(f"  Total: {total_files}")
    print(f"  {Colors.OKGREEN}Matches (Pass): {matches}{Colors.ENDC}")
    print(f"  {Colors.FAIL}Mismatches (Fail): {mismatches}{Colors.ENDC}")
    print(f"  {Colors.WARNING}Timeouts: {timeouts}{Colors.ENDC}")

if __name__ == "__main__":
    main()