import re
data = open('static/chatbot/assets/index.js', encoding='utf-8').read()
idx = data.find('className:`sidebar')
if idx == -1:
    idx = data.find('className:"sidebar')
with open('scratch/sidebar.txt', 'w', encoding='utf-8') as f:
    f.write(data[max(0, idx-100):idx+800])
