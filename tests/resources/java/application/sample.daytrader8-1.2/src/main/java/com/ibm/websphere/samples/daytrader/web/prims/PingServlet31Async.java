/**
 * (C) Copyright IBM Corporation 2015.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.ibm.websphere.samples.daytrader.web.prims;

import java.io.IOException;

import java.util.Queue;
import java.util.concurrent.LinkedBlockingQueue;

import javax.servlet.AsyncContext;
import javax.servlet.ReadListener;
import javax.servlet.ServletConfig;
import javax.servlet.ServletException;
import javax.servlet.ServletInputStream;
import javax.servlet.ServletOutputStream;
import javax.servlet.WriteListener;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

//import com.ibm.websphere.samples.daytrader.util.Log;

/**
 *
 * PingServlet31Async tests fundamental dynamic HTML creation functionality through
 * server side servlet processing asynchronously with non-blocking i/o.
 *
 */

@WebServlet(name = "PingServlet31Async", urlPatterns = { "/servlet/PingServlet31Async" }, asyncSupported=true)
public class PingServlet31Async extends HttpServlet {

    private static final long serialVersionUID = 8731300373855056660L;
    private static String initTime;
    private static int hitCount;

    /**
     * forwards post requests to the doGet method Creation date: (11/6/2000
     * 10:52:39 AM)
     *
     * @param res
     *            javax.servlet.http.HttpServletRequest
     * @param res2
     *            javax.servlet.http.HttpServletResponse
     */
    @Override
    public void doPost(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {
        res.setContentType("text/html");
                
        AsyncContext ac = req.startAsync();
           
        ServletInputStream input = req.getInputStream();
        ReadListener readListener = new ReadListenerImpl(input, res, ac);
        input.setReadListener(readListener);
    }

    class ReadListenerImpl implements ReadListener {
        private ServletInputStream input = null;
        private HttpServletResponse res = null;
        private AsyncContext ac = null;
        private Queue<String> queue = new LinkedBlockingQueue<String>();

        ReadListenerImpl(ServletInputStream in, HttpServletResponse r, AsyncContext c) {
            input = in;
            res = r;
            ac = c;
        }
    
        public void onDataAvailable() throws IOException {
            StringBuilder sb = new StringBuilder();
            int len = -1;
            byte b[] = new byte[1024];
            
            while (input.isReady() && (len = input.read(b)) != -1) {
                String data = new String(b, 0, len);
                sb.append(data);
            }
            queue.add(sb.toString());
            
        }
    
        public void onAllDataRead() throws IOException {
            ServletOutputStream output = res.getOutputStream();
            WriteListener writeListener = new WriteListenerImpl(output, queue, ac);
            output.setWriteListener(writeListener);
        }
    
        public void onError(final Throwable t) {
            ac.complete();
            t.printStackTrace();
        }
    }
    
    class WriteListenerImpl implements WriteListener {
        private ServletOutputStream output = null;
        private Queue<String> queue = null;
        private AsyncContext ac = null;

        WriteListenerImpl(ServletOutputStream sos, Queue<String> q, AsyncContext c) {
            output = sos;
            queue = q;
            ac = c;
            
            try {
                output.print("<html><head><title>Ping Servlet 3.1 Async</title></head>"
                        + "<body><hr/><br/><font size=\"+2\" color=\"#000066\">Ping Servlet 3.1 Async</font>"
                        + "<br/><font size=\"+1\" color=\"#000066\">Init time : " + initTime
                        + "</font><br/><br/><b>Hit Count: " + ++hitCount + "</b><br/>Data Received: ");
            } catch (IOException e) {
                // TODO Auto-generated catch block
                e.printStackTrace();
            }
        }

        public void onWritePossible() throws IOException {
            
            while (queue.peek() != null && output.isReady()) {
                String data = (String) queue.poll();
                output.print(data);
            }
            
            if (queue.peek() == null) {
                output.println("</body></html>");
                ac.complete();
            }
        }

        public void onError(final Throwable t) {
            ac.complete();
            t.printStackTrace();
        }
    }
        


    /**
     * this is the main method of the servlet that will service all get
     * requests.
     *
     * @param request
     *            HttpServletRequest
     * @param responce
     *            HttpServletResponce
     **/
    @Override
    public void doGet(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {
        doPost(req,res);          
    }
    /**
     * returns a string of information about the servlet
     *
     * @return info String: contains info about the servlet
     **/
    @Override
    public String getServletInfo() {
        return "Basic dynamic HTML generation through a servlet";
    }

    /**
     * called when the class is loaded to initialize the servlet
     *
     * @param config
     *            ServletConfig:
     **/
    @Override
    public void init(ServletConfig config) throws ServletException {
        super.init(config);
        initTime = new java.util.Date().toString();
        hitCount = 0;

    }
}