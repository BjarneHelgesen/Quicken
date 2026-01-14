@echo off
python -m cProfile -o happy.prof test\happy_paths.py

REM Extract top 40 entries sorted by cumulative time 
python -c "import pstats; p = pstats.Stats('happy.prof'); p.sort_stats('cumtime'); p.print_stats(40)" > prof_results.txt

claude --print "Analyze the following cProfile results for test\happy_paths.py. Focus on: 1) The cache HIT path (fast path when cached results exist) 2) The cache MISS path (slow path when tool must be executed). Identify bottlenecks in both. 

del prof_results.txt