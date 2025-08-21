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

import javax.servlet.AsyncContext;
import javax.servlet.ReadListener;
import javax.servlet.ServletConfig;
import javax.servlet.ServletException;
import javax.servlet.ServletInputStream;
import javax.servlet.ServletOutputStream;
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

@WebServlet(name = "PingServlet31AsyncRead", urlPatterns = { "/servlet/PingServlet31AsyncRead" }, asyncSupported=true)
public class PingServlet31AsyncRead extends HttpServlet {

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
        private StringBuilder sb = new StringBuilder();

        ReadListenerImpl(ServletInputStream in, HttpServletResponse r, AsyncContext c) {
            input = in;
            res = r;
            ac = c;
        }
    
        public void onDataAvailable() throws IOException {
            
            int len = -1;
            byte b[] = new byte[1024];
            
            while (input.isReady() && (len = input.read(b)) != -1) {
                String data = new String(b, 0, len);
                sb.append(data);
            }
            
            
        }
    
        public void onAllDataRead() throws IOException {
            ServletOutputStream output = res.getOutputStream();
            output.println("<html><head><title>Ping Servlet 3.1 Async</title></head>"
                    + "<body><hr/><br/><font size=\"+2\" color=\"#000066\">Ping Servlet 3.1 AsyncRead</font>"
                    + "<br/><font size=\"+1\" color=\"#000066\">Init time : " + initTime
                    + "</font><br/><br/><b>Hit Count: " + ++hitCount + "</b><br/>Data Received: " + sb.toString() + "</body></html>");
            ac.complete();
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