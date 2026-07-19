import re
data = open('static/chatbot/assets/index.js', encoding='utf-8').read()
idx = data.find("I couldn't play audio right now")
with open('scratch/out.txt', 'w', encoding='utf-8') as f:
    f.write(data[max(0, idx-1000):idx+1000])
